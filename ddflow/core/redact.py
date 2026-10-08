"""One Redactor, named profiles (D-unify 6, B-uni-textkit.4).

Profiles: `log` (the committed event log: new writes only), `view` (committed views),
`export` (exported documents) and `upstream` (anything leaving the machine in a report);
the shape of a command line is structural, `redact_argv`, not a profile. Every text profile is the full one: secrets, private
addresses and hosts, home paths, emails and names go; they differ only in which
machine-local inputs they read (see `PROFILES`).

The engine, from `services/redact_report`, which re-exports it:

Redaction for anything that leaves the machine in an upstream report.

Per D-upstream-reporting (3): a text pass whose only inputs are the text and the environment
(`$HOME`, the machine name, the working directory; each overridable) and which writes nothing -- that removes secrets, private addresses and hosts, home
and repo paths, the machine hostname, emails and project names, leaving a visible
`[REDACTED:<kind>]` marker so a reader can see that something was there, and returns a
count per kind so a preview can say what was removed.

Properties the callers rely on:

* idempotent -- a marker is never scanned again, so redacting twice changes nothing;
* total -- odd input (None, bytes, lone surrogates, huge text) never raises;
* conservative about versions -- `v0.11.0.1` and `0.1.7` are not addresses.

The secret patterns are the session redaction patterns (`redact_patterns` plus
`redact_extra`), passed in by the caller. A bad pattern is a configuration error and raises:
a control that silently stops matching is the failure mode that matters.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

#: The tool's own names are public, and a report about ddflow must still say "ddflow".
PUBLIC_NAMES = frozenset({"ddflow", "ddflow-mcp", "ddflow_mcp"})

#: Machine names that identify nothing.
_GENERIC_HOSTS = frozenset({"localhost", "localdomain", "ubuntu", "debian", "host", "runner"})

#: Shorter names would redact ordinary words.
_MIN_TERM = 3
_V4 = 4

_MARKER = re.compile(r"\[REDACTED(?::[a-z0-9_]*)?\]?")

#: Same detectors as tests/test_repo_is_generic.py.
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:])")

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
#: `/home/<user>/...` and `/Users/<user>/...`: the whole path token, since what follows
#: the user is usually a project directory.
_HOME_PATH = re.compile(r"(?<![\w.-])/(?:home|Users)/[^\s/'\"`<>)\]},;:]+(?:/[^\s'\"`<>)\]},;]*)?")
_TILDE_PATH = re.compile(r"(?<![\w.~-])~/[^\s'\"`<>)\]},;]+")
#: A private-network host name. Not when a file extension follows (`settings.local.json`).
_HOST = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"(?:lan|local|internal|home\.arpa)(?![\w-]|\.\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Redacted:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _this_network(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.version == _V4 and ip.packed[0] == 0


def _parse(raw: str, ip_type: type):
    """`ipaddress` rejects zero-padded octets, but a reader sees one."""
    if ip_type is ipaddress.IPv4Address:
        raw = ".".join(part.lstrip("0") or "0" for part in raw.split("."))
    return ip_type(raw)


def _is_private(raw: str, ip_type: type) -> bool:
    try:
        ip = _parse(raw, ip_type)
    except ValueError:
        return False
    if ip.is_unspecified or _this_network(ip):
        return False
    return ip.is_private or ip.is_link_local or ip.is_loopback


def private_addresses(text: str) -> set[str]:
    """Private/link-local IPv4 and IPv6 literals in `text`.

    The repo guard's rules, plus zero-padded octets.

    Loopback is excluded here to match tests/test_repo_is_generic.py; `redact_report`
    removes it as well, since a report has no use for it either.
    """
    found = set()
    for pat, kind in ((_IPV4, ipaddress.IPv4Address), (_IPV6, ipaddress.IPv6Address)):
        for m in pat.finditer(text):
            raw = m.group(1)
            try:
                ip = _parse(raw, kind)
            except ValueError:
                continue
            if _is_private(raw, kind) and not ip.is_loopback:
                found.add(raw)
    return found


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
            raise ValueError(
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


def redact_text(
    text: object,
    *,
    secret_patterns: Iterable[str],
    hostname: str | None = None,
    names: Iterable[str] = (),
    home: str | None = None,
    repo_root: str | None = None,
    secret_style: str = "marker",
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
    """
    p = _Pass(_coerce(text))
    _secrets(p, secret_patterns, secret_style)

    p.sub(_EMAIL, "email")

    root = _coerce(repo_root)
    home_dir = _coerce(home)
    # The repo root keeps its tail: `<root>/ddflow/x.py` is a useful frame.
    if root not in ("", "/", "."):
        p.sub(re.compile(re.escape(root) + r"(?![\w.-])"), "path")
    if home_dir and home_dir not in ("/", "~", ".", ""):
        p.sub(
            re.compile(
                r"(?<![\w.-])" + re.escape(home_dir) + r"(?:/[^\s'\"`<>)\]},;]*)?(?![\w.-])"
            ),
            "path",
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


_KEY = re.compile(r"[A-Za-z0-9_. -]*")
#: Authorization schemes: `<scheme> <secret>`, no key separator.
_SCHEMES = frozenset({"bearer", "basic", "token"})
#: A `Bearer <token>` match splits into exactly two parts: the scheme and the secret.
_SCHEME_AND_VALUE = 2


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


@dataclass(frozen=True)
class Profile:
    """Which machine-local inputs a profile removes. `None` means "the machine's own",
    which `services.redact_report.redactor` resolves from the environment; `""` means
    "none", so the output is byte-identical on every machine."""

    name: str
    hostname: str | None
    home: str | None
    repo_root: str | None
    secrets: str = "marker"


#: `repo_root` is empty where the repository's own directory name is the project's and stays.
PROFILES: Mapping[str, Profile] = {
    p.name: p
    for p in (
        # The committed event log: the machine's own hostname and $HOME go as well, and
        # secrets keep their context.
        Profile("log", None, None, "", secrets="mask"),
        # A committed view: byte-identical on every machine (roborev on 9b6db6bc).
        Profile("view", "", "", ""),
        # An exported document: the rendering machine's hostname and $HOME also go.
        Profile("export", None, None, ""),
        # A report that leaves the machine: everything of the machine's own goes.
        Profile("upstream", None, None, None),
    )
}
#: `argv` is structural (an allowlist, `redact_argv`), not a text profile: no Redactor.

_PROGRAMS = frozenset({"ddflow", "ddflow-mcp", "python", "python3", "uv", "uvx"})
_WORD = re.compile(r"[a-z][a-z0-9_-]{0,23}")


class Redactor:
    """`Redactor("view", secret_patterns=...).text(s)`; `services.redact_report.redactor`
    builds one from the config."""

    def __init__(
        self,
        profile: str,
        *,
        secret_patterns: Iterable[str],
        names: Iterable[str] = (),
        hostname: str | None = None,
        home: str | None = None,
        repo_root: str | None = None,
    ) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown redaction profile {profile!r}; one of {', '.join(PROFILES)}")
        base = PROFILES[profile]
        self.profile = base
        self.secret_patterns = list(secret_patterns)
        self.names = list(names)
        self.hostname = base.hostname if hostname is None else hostname
        self.home = base.home if home is None else home
        self.repo_root = base.repo_root if repo_root is None else repo_root

    def text(self, text: object) -> Redacted:
        return redact_text(
            text,
            secret_patterns=self.secret_patterns,
            hostname=self.hostname,
            names=self.names,
            home=self.home,
            repo_root=self.repo_root,
            secret_style=self.profile.secrets,
        )


def redact_argv(
    argv: Sequence[str],
    subcommands: Iterable[str] | None = None,
    flags: Iterable[str] | None = None,
) -> list[str]:
    """The shape of a command line, from allowlists and nothing else.

    Kept: the program (when it is ddflow or a Python launcher), the leading words the
    caller lists in `subcommands` (the CLI's own verbs), and the flag names the caller
    lists in `flags` (the CLI's own flags, matched exactly, with or without `=value`).
    Everything else is a placeholder: `<flag>` for any other dash-led token, `<value>`
    for a word right after a kept flag, `<arg>` for any other word. Nothing is inferred
    from how a token looks -- a dash-led secret (`-hunter2`, `-psecret`, `--s3cr3t`) cannot
    be told from a flag, so it is only ever kept if the caller named it as one."""
    allowed = set(subcommands) if subcommands is not None else set()
    known = {"-m", *(flags or ())}  # `-m` is the Python launcher's own
    out: list[str] = []
    leading = True
    for i, raw in enumerate(argv):
        tok = str(raw)
        if i == 0:
            base = tok.replace("\\", "/").rsplit("/", 1)[-1]
            out.append(base if base in _PROGRAMS else "<program>")
        elif tok in _PROGRAMS and out[-1] == "-m":
            out.append(tok)  # `python -m ddflow`
        elif tok.startswith("-") and tok != "-":
            leading = False
            name, eq, _value = tok.partition("=")
            shown = name if name in known else "<flag>"
            out.append(f"{shown}=<value>" if eq else shown)
        elif leading and _WORD.fullmatch(tok) and tok in allowed:
            out.append(tok)
        else:
            leading = False
            after_flag = out[-1] in known and "=" not in out[-1]
            out.append("<value>" if after_flag else "<arg>")
    return out


# -- the committed log: which fields of which event kind are free text (D-unify 6) ----------

#: Event kind -> the `data` fields that hold free text. `EventLog._write` runs these through
#: the `log` profile, so a secret, a LAN address, the machine's hostname or a home path in a
#: gate's output tail, a bug summary or a lesson is never committed (bug B5deba76d04).
#: A string field is redacted whole; a dict or list field is redacted leaf by leaf, leaving
#: the lookup keys of `_KEEP_KEYS` alone. A field NOT listed is an id, a digest, a path or a
#: number that something looks up by, and is written as given.
LOG_TEXT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "approval.granted": ("note",),
    "bug.fixed": ("lesson", "changelog", "regression_verify"),
    "bug.found": ("summary", "title"),
    "bug.invalid": ("evidence", "reason"),
    "bug.reopened": ("reason",),
    "cadence.ran": ("evidence", "result"),
    "ci.result": ("checks",),
    "decision.recorded": ("alternatives", "consequences", "context", "decision", "title"),
    "decision.superseded": ("reason",),
    "def.merged": ("reason",),
    "def.recorded": ("fields",),
    "def.retired": ("reason",),
    "def.superseded": ("reason",),
    "def.updated": ("fields",),
    "flow.chosen": ("reason",),
    "gate.failed": ("evidence", "reason"),
    "gate.partial": ("evidence", "reason"),
    "gate.passed": ("evidence", "reason"),
    "gate.skipped": ("evidence", "reason"),
    "gate.unavailable": ("evidence", "reason"),
    "item.abandoned": ("reason",),
    "item.blocked": ("reason",),
    "item.completed": ("changelog", "evidence"),
    "item.reopened": ("reason",),
    "item.resolved": ("definition",),
    "item.unblocked": ("note",),
    "job.ended": ("note",),
    "job.started": ("command",),
    "lease.expired": ("reason",),
    "lease.released": ("note", "reason"),
    "lesson.recorded": ("how", "pattern", "rule", "summary", "title", "why"),
    "memory.forgotten": ("reason",),
    "memory.recorded": ("text",),
    "phase.added": ("body", "title", "line"),
    "phase.updated": ("body", "title"),
    "pr.synced": ("feedback",),
    "record.extended": ("text",),
    "research.recorded": ("claim", "falsifier", "mechanism", "probe", "probe_output", "question"),
    "review.triaged": ("title", "probe"),
    "schedule.defined": ("title",),
    "schedule.removed": ("reason",),
    "session.ended": ("summary",),
    "session.note": ("text",),
    "session.prompt": ("text",),
    "session.started": ("cwd",),
    "skew.overridden": ("reason",),
    "task.added": ("body", "title", "line"),
    "task.removed": ("reason",),
    "task.updated": ("body", "title"),
    "trigger.evaluated": ("errors",),
    "trigger.suppressed": ("detail", "reason"),
}

#: Kinds whose payload is ids, digests, paths, flags and numbers only. Declared so a NEW kind
#: must choose (tests/test_log_redaction_all_kinds.py).
LOG_NO_TEXT = frozenset(
    {
        "approval.used",
        "backmerge.recorded",
        "ddflow.seen",
        "deploy.recorded",
        "export.disabled",
        "export.enabled",
        "gate.out_of_order",
        "gate.started",
        "item.started",
        # a lease note is ddflow's own ("re-homed from <agent id>"): the agent id is a lookup
        "lease.acquired",
        "lease.renewed",
        "link.recorded",
        "pr.opened",
        "reviewer.configured",
        "trigger.fired",
        "worktree.adopted",
        "worktree.created",
        "worktree.merged",
        "worktree.removed",
    }
)

#: Kinds the fixture pins NO fields for (their writers pass free-form payloads: a sibling
#: repository's item title, a git error): every field is treated as text, like an unknown kind.
LOG_ALL_TEXT = frozenset(
    {
        "bug.reported_upstream",
        "export.acknowledged",
        "external.observed",
        "phase.removed",
        "pr.changes_requested",
        "pr.closed",
        "pr.merged",
        "port.applied",
        "release.closed",
        "release.opened",
        "release.tagged",
        "repair.applied",
        "reviewer.approved",
        "schedule.updated",
        "upgrade.applied",
    }
)

#: Keys whose string value is a path something opens or compares (a worktree, an output log):
#: only secrets are masked there, so the path still resolves. Every other leaf of a text
#: field is redacted whatever its key, since keys come from data (gate names, `def` fields).
_PATH_KEYS = frozenset({"worktree", "output_file", "output_log", "source_tree", "path"})


def redact_leaves(value: object, redactor: Redactor, key: str = "") -> object:
    """``value`` with every string leaf redacted; under a `_PATH_KEYS` key, only its secrets."""
    if isinstance(value, str):
        if key in _PATH_KEYS:
            return mask_secrets(value, redactor.secret_patterns)[0]
        return redactor.text(value).text
    if isinstance(value, Mapping):
        return {k: redact_leaves(v, redactor, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_leaves(v, redactor, key) for v in value]
    return value


def redact_event_data(kind: str, data: dict, redactor: Redactor) -> dict:
    """``data`` of an event of ``kind`` with its declared text fields redacted (new dict).

    A kind in neither `LOG_TEXT_FIELDS` nor `LOG_NO_TEXT` (a test refuses one in the
    vocabulary) has EVERY field treated as text: unknown is not the same as text-free."""
    if kind in LOG_NO_TEXT:
        return data
    names = None if kind in LOG_ALL_TEXT else LOG_TEXT_FIELDS.get(kind)
    if names is not None and not any(n in data for n in names):
        return data
    return {
        k: redact_leaves(v, redactor, k) if names is None or k in names else v
        for k, v in data.items()
    }
