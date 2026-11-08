"""The marker-aware text pass and the full text redaction built on it.

`redact_text` is the whole profile: secrets, private addresses and hosts, home paths,
emails and names go; the profiles differ only in which machine-local inputs they read
(see `profiles`). The pass never raises and is idempotent: a marker is never scanned
again.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..redact_errors import PatternError
from .pattern import (
    _EMAIL,
    _GENERIC_HOSTS,
    _HOME_PATH,
    _HOST,
    _IPV4,
    _IPV6,
    _MARKER,
    _MIN_TERM,
    _TILDE_PATH,
    PUBLIC_NAMES,
    _is_private,
)

_KEY = re.compile(r"[A-Za-z0-9_. -]*")
#: Authorization schemes: `<scheme> <secret>`, no key separator.
_SCHEMES = frozenset({"bearer", "basic", "token"})
#: A `Bearer <token>` match splits into exactly two parts: the scheme and the secret.
_SCHEME_AND_VALUE = 2


@dataclass(frozen=True)
class Redacted:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _term(word: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.IGNORECASE)


def _marker(kind: str) -> str:
    return f"[REDACTED:{kind}]"


class _Pass:
    """Applies substitutions to the text between markers only, and counts them."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.counts: dict[str, int] = {}

    def sub(
        self, pattern: re.Pattern[str], kind: str, keep=None, fill=None, whole: bool = False
    ) -> None:
        """Replace matches outside markers; `keep(match)` true leaves a match alone;
        `fill(text)` builds the replacement from the matched text (default: a marker).
        `whole` scans the text unsplit, so a match may straddle a marker (a secret must not
        keep its tail because a marker sits inside it); `keep` then guards idempotence."""
        n = 0

        def repl(m: re.Match[str]) -> str:
            nonlocal n
            if keep is not None and keep(m):
                return m.group(0)
            n += 1
            return fill(m.group(0)) if fill is not None else _marker(kind)

        if whole:
            self.text = pattern.sub(repl, self.text)
            if n:
                self.counts[kind] = self.counts.get(kind, 0) + n
            return
        pieces = _MARKER.split(self.text)
        marks = _MARKER.findall(self.text)
        out = []
        for i, piece in enumerate(pieces):
            out.append(pattern.sub(repl, piece))
            if i < len(marks):
                out.append(marks[i])
        self.text = "".join(out)
        if n:
            self.counts[kind] = self.counts.get(kind, 0) + n


def _coerce(text: object) -> str:
    if text is None:
        return ""
    if isinstance(text, (bytes, bytearray)):
        return bytes(text).decode("utf-8", "replace")
    try:
        return text if isinstance(text, str) else str(text)
    except Exception:  # a hostile __str__ must not break a redaction
        return ""


def _secrets(p: _Pass, patterns: Iterable[str], style: str) -> None:
    for pat in patterns:
        try:
            compiled = re.compile(pat)
        except re.error as exc:
            raise PatternError(
                f"redaction pattern {pat!r} does not compile: {exc}.\n"
                f"If you set [session].redact_patterns from an environment variable, a "
                f"regex containing a comma (e.g. the quantifier '{{16,}}') is split on "
                f"it. Pass the list as JSON instead:\n"
                f"""  DDFLOW_SESSION_REDACT_PATTERNS='["pattern one", "pattern two"]'"""
            ) from exc
        fill = _mask if style == "mask" else None

        def settled(m: re.Match[str], fill=fill) -> bool:
            # a marker, or a masked value, from an earlier pass: scanning it again changes nothing
            return _MARKER.fullmatch(m.group(0)) is not None or (
                fill is not None and fill(m.group(0)) == m.group(0)
            )

        p.sub(compiled, "secret", keep=settled, fill=fill, whole=True)


def mask_secrets(text: object, secret_patterns: Iterable[str]) -> tuple[str, int]:
    """Only the secrets, context-preserving (`api_key: [REDACTED]`); (clean text, count)."""
    p = _Pass(_coerce(text))
    _secrets(p, secret_patterns, "mask")
    return p.text, p.counts.get("secret", 0)


def _home_fill(home_dir: str, home_style: str):
    """The replacement for the machine's own home path, or `None` for the default marker.

    `home_style` "resolvable" (the committed-log profile) replaces the home prefix with
    `$HOME`, keeping the tail, so a path the operator must recover stays addressable
    (bug B-redaction-hides-parked-work-path) instead of turning into the opaque
    `[REDACTED:path]`.
    """
    if home_style != "resolvable":
        return None
    n = len(home_dir)

    def fill(matched: str) -> str:
        return "$HOME" + matched[n:]

    return fill


def redact_text(
    text: object,
    *,
    secret_patterns: Iterable[str],
    hostname: str | None = None,
    names: Iterable[str] = (),
    home: str | None = None,
    repo_root: str | None = None,
    secret_style: str = "marker",
    home_style: str = "marker",
) -> Redacted:
    """Redact `text` for an upstream report. Returns the text and a count per kind.

    Kinds: `secret`, `email`, `path`, `ipv4`, `ipv6`, `host`, `hostname`, `name`.

    `names` is the caller's list of project names (literal words, matched
    case-insensitively on word boundaries); the caller adds `[upstream].redact_extra`
    once that section exists. The repo directory name is added here. `hostname`, `home`
    and `repo_root` are the values to remove (`None` or empty: none; the services layer
    resolves "the machine's own" before it calls, since `core` reads no environment).
    Secrets use `secret_patterns` (the
    session `redact_patterns` and `redact_extra`; the services layer supplies them from the
    config, since `core` cannot read it).
    `secret_style` "mask" keeps the surrounding words (`api_key: [REDACTED]`, the event
    log's style); "marker" (the default) replaces the match by `[REDACTED:secret]`.
    `home_style` "resolvable" renders the machine's own home path as `$HOME` (keeping the
    tail) instead of the `[REDACTED:path]` marker; the default "marker" is the opaque form.
    """
    p = _Pass(_coerce(text))
    _secrets(p, secret_patterns, secret_style)

    p.sub(_EMAIL, "email")

    root = _coerce(repo_root)
    home_dir = _coerce(home).rstrip("/")
    # A trailing slash is the same home (and `/` collapses to nothing); the fill measures the
    # match from `len(home_dir)`, so it must be the same value the regex escaped or a
    # `/`-led tail would lose its separator (roborev on the trailing-slash home).
    # The repo root keeps its tail: `<root>/ddflow/x.py` is a useful frame.
    if root not in ("", "/", "."):
        p.sub(re.compile(re.escape(root) + r"(?![\w.-])"), "path")
    if home_dir and home_dir not in ("/", "~", ".", ""):
        p.sub(
            re.compile(
                r"(?<![\w.-])" + re.escape(home_dir) + r"(?:/[^\s'\"`<>)\]},;]*)?(?![\w.-])"
            ),
            "path",
            fill=_home_fill(home_dir, home_style),
        )
    p.sub(_HOME_PATH, "path")
    p.sub(_TILDE_PATH, "path")

    for pat, kind, ip_type in (
        (_IPV4, "ipv4", ipaddress.IPv4Address),
        (_IPV6, "ipv6", ipaddress.IPv6Address),
    ):
        p.sub(pat, kind, keep=lambda m, t=ip_type: not _is_private(m.group(1), t))

    p.sub(_HOST, "host")

    for h in _host_forms(_coerce(hostname)):
        p.sub(_term(h), "hostname")

    words: set[str] = set()
    for raw in [
        *names,
        root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if root not in ("", ".") else "",
    ]:
        word = _coerce(raw).strip()
        if len(word) >= _MIN_TERM and word.lower() not in PUBLIC_NAMES:
            words.add(word)
    for w in sorted(words, key=len, reverse=True):
        p.sub(_term(w), "name")
    return Redacted(p.text, dict(p.counts))


def _host_forms(machine: str) -> list[str]:
    machine = _coerce(machine).strip()
    forms = {machine, machine.split(".")[0]} if machine else set()
    # longest first: the short label would otherwise cut into the full name, by hash order
    return sorted(
        (f for f in forms if len(f) >= _MIN_TERM and f.lower() not in _GENERIC_HOSTS),
        key=lambda f: (-len(f), f),
    )


def _mask(s: str) -> str:
    """Replace the secret, keeping enough context that the line still reads.

    `api_key: abc` becomes `api_key: [REDACTED]` and `Bearer abc` becomes
    `Bearer [REDACTED]`, so a reader (or a replay) can still see WHAT was supplied
    without the value. A whole-match blanking would make the surrounding prompt
    ungrammatical and harder to follow months later.
    """
    scheme, _, rest = s.partition(" ")
    if scheme.lower() in _SCHEMES and rest.strip() and rest.lstrip()[0] not in ":=":
        return f"{scheme} [REDACTED]"  # `Bearer abc:def`: the value may hold a separator
    cuts = [i for i in (s.find(":"), s.find("=")) if i >= 0]
    if cuts:  # the FIRST separator ends the key; a later one belongs to the value
        head, sep, tail = s[: min(cuts)], s[min(cuts)], s[min(cuts) + 1 :]
        # a key reads as words; anything else before the separator is part of the secret
        if _KEY.fullmatch(head.strip()) and tail.strip("=: \t"):  # not only padding (`abc==`, `:`)
            return f"{head}{sep} [REDACTED]"
    parts = s.split(None, 1)
    if len(parts) == _SCHEME_AND_VALUE:
        return f"{parts[0]} [REDACTED]"
    return "[REDACTED]"
