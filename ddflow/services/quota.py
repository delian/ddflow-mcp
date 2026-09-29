"""Usage quotas: what each agent or LLM may spend, per window (D-quotas).

A quota belongs to whoever PAYS for the tokens -- a Claude account, an API key, a
self-hosted endpoint -- not to a project, so profiles live in one user-level file and
every ddflow project on this machine reads the same one. Committing them to a project's
log would hand one account's limits to every clone and let two projects disagree about
one account.

A SUBJECT is `agent:<harness>/<account>` (a coding agent logged in to a plan) or
`llm:<base_url>` (a model endpoint). Its PROFILE is exactly one of:

* **unlimited** -- a self-hosted model, a flat-rate endpoint: no quota applies, ever;
* **unknown** -- the agent was asked and could not tell, so the OPERATOR must be asked.
  Recorded rather than left blank, because "never asked" and "asked, nobody knows" call
  for different next steps;
* **windows** -- limits per `5h | day | week | month | total`, each in `tokens | usd |
  sessions | percent`. `percent` means the vendor reports utilisation itself (Claude
  Code's five-hour and weekly windows), so its limit is always 100.

The agent is asked first and the operator decides: an agent may not overwrite a profile
the operator declared. An unreadable store is an ERROR, never "no quotas": reading a
corrupt file as empty would lift every limit without a word.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..infra.tomlcfg import atomic_write, locked

#: Window -> its length in seconds. `month` is 30 days when rolling; a FIXED window
#: resets at its anchor plus whole periods. `total` never resets.
WINDOWS: dict[str, int | None] = {
    "5h": 5 * 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "total": None,
}
UNITS = ("tokens", "usd", "sessions", "percent")
RESETS = ("rolling", "fixed")
DECLARERS = ("agent", "operator")
STORE_VERSION = 1
#: A `percent` window is the vendor's own utilisation, so its limit is always this.
PERCENT_LIMIT = 100.0

_SUBJECT = re.compile(r"^(agent|llm):\S+$")
_SCALE = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}


class QuotaError(ValueError):
    """A declaration or a store that cannot be accepted, with the reason."""


class OperatorOwned(QuotaError):
    """An agent tried to replace a profile the operator declared: a refusal, not an error."""


@dataclass(frozen=True)
class Window:
    window: str
    limit: float
    unit: str
    reset: str = "rolling"
    #: ISO-8601 start of a FIXED window's first period; "" for a rolling one.
    anchor: str = ""

    def seconds(self) -> int | None:
        return WINDOWS[self.window]


@dataclass(frozen=True)
class Profile:
    subject: str
    unlimited: bool = False
    unknown: bool = False
    windows: tuple[Window, ...] = ()
    declared_by: str = "agent"
    at: str = ""
    note: str = ""

    @property
    def state(self) -> str:
        """`unlimited`, `unknown` or `limited` -- the one question callers ask first."""
        if self.unlimited:
            return "unlimited"
        return "unknown" if self.unknown else "limited"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["windows"] = [asdict(w) for w in self.windows]
        d["state"] = self.state
        return d


def account_tag(account_id: str) -> str:
    """A stable short tag for an account id, so a subject never carries the raw id."""
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(text: str) -> str:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise QuotaError(f"anchor {text!r} is not an ISO-8601 time") from exc


def _number(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMgG]?)", text.strip())
    if not m:
        raise QuotaError(f"limit {text!r} is not a number (k, M and G suffixes allowed)")
    return float(m.group(1)) * _SCALE[m.group(2).lower()]


def parse_windows(spec: str) -> tuple[Window, ...]:
    """Windows from the one-line form every surface accepts, comma-separated:

    `5h=percent`            the vendor reports utilisation for this window
    `day=2M:tokens`         2,000,000 tokens per rolling day
    `month=50:usd@2026-10-01T00:00Z`   $50 per month, periods starting at the anchor
    `week=40:sessions`      40 agent sessions per rolling week
    """
    out: list[Window] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        name, sep, rest = part.partition("=")
        if not sep:
            raise QuotaError(f"{part!r}: expected <window>=<limit>:<unit> or <window>=percent")
        rest, _, anchor = rest.partition("@")
        if rest.strip() == "percent":
            limit, unit = PERCENT_LIMIT, "percent"
        else:
            amount, sep, unit = rest.partition(":")
            if not sep:
                raise QuotaError(f"{part!r}: name the unit, e.g. {name}={amount}:tokens")
            limit, unit = _number(amount), unit.strip()
        out.append(
            Window(
                window=name.strip(),
                limit=limit,
                unit=unit,
                reset="fixed" if anchor else "rolling",
                anchor=anchor.strip(),
            )
        )
    return tuple(out)


def validate(p: Profile) -> Profile:
    """The profile, normalised; raises QuotaError naming the first thing wrong."""
    if not _SUBJECT.match(p.subject):
        raise QuotaError(
            f"subject {p.subject!r} must be agent:<harness>/<account> or llm:<base_url>"
        )
    if p.declared_by not in DECLARERS:
        raise QuotaError(f"declared_by must be one of {', '.join(DECLARERS)}")
    kinds = [p.unlimited, p.unknown, bool(p.windows)]
    if sum(kinds) != 1:
        raise QuotaError("a profile is exactly one of: unlimited, unknown, or some windows")
    seen: set[tuple[str, str]] = set()
    windows = []
    for w in p.windows:
        if w.window not in WINDOWS:
            raise QuotaError(f"window {w.window!r} is not one of {', '.join(WINDOWS)}")
        if w.unit not in UNITS:
            raise QuotaError(f"unit {w.unit!r} is not one of {', '.join(UNITS)}")
        if w.reset not in RESETS:
            raise QuotaError(f"reset {w.reset!r} is not one of {', '.join(RESETS)}")
        if w.unit == "percent" and w.limit != PERCENT_LIMIT:
            raise QuotaError("a percent window is the vendor's own utilisation: its limit is 100")
        if not w.limit > 0:
            raise QuotaError(f"{w.window}: a limit must be positive (declare unlimited instead)")
        if w.reset == "fixed" and w.window == "total":
            raise QuotaError("a total never resets, so it cannot have a fixed anchor")
        if w.reset == "fixed" and not w.anchor:
            raise QuotaError(f"{w.window}: a fixed window needs an anchor time")
        if (w.window, w.unit) in seen:
            raise QuotaError(f"{w.window} in {w.unit} is declared twice")
        seen.add((w.window, w.unit))
        windows.append(Window(**{**asdict(w), "anchor": _parse_time(w.anchor) if w.anchor else ""}))
    return Profile(**{**asdict(p), "windows": tuple(windows), "at": p.at or _now()})


# -- the store ---------------------------------------------------------------------


def store_path() -> Path:
    """`$XDG_CONFIG_HOME/ddflow/quotas.json`, else `~/.config/ddflow/quotas.json`."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ddflow" / "quotas.json"


def _from_dict(d: dict[str, Any]) -> Profile:
    return Profile(
        subject=d["subject"],
        unlimited=bool(d.get("unlimited", False)),
        unknown=bool(d.get("unknown", False)),
        windows=tuple(Window(**w) for w in d.get("windows", [])),
        declared_by=d.get("declared_by", "agent"),
        at=d.get("at", ""),
        note=d.get("note", ""),
    )


def load(path: Path | None = None) -> dict[str, Profile]:
    """Every declared profile, by subject. Missing file -> {}. Unreadable -> QuotaError."""
    path = path or store_path()
    try:
        raw = path.read_text("utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise QuotaError(f"cannot read {path}: {exc}") from exc
    try:
        doc = json.loads(raw)
        if doc.get("version") != STORE_VERSION:
            raise QuotaError(f"{path}: unsupported version {doc.get('version')!r}")
        return {s: validate(_from_dict(d)) for s, d in doc["profiles"].items()}
    except QuotaError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise QuotaError(
            f"{path} is not a readable quota store ({exc}); fix or remove it -- it is not "
            f"read as 'no quotas', because that would lift every limit silently"
        ) from exc


def _dump(profiles: Iterable[Profile]) -> str:
    doc = {
        "version": STORE_VERSION,
        "profiles": {
            p.subject: {k: v for k, v in p.to_dict().items() if k != "state"}
            for p in sorted(profiles, key=lambda p: p.subject)
        },
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def declare(profile: Profile, path: Path | None = None) -> tuple[Profile, Profile | None]:
    """Record a profile. Returns (the stored profile, the one it replaced or None).

    The operator decides: an AGENT's declaration never replaces an OPERATOR's.
    """
    path = path or store_path()
    new = validate(profile)
    with locked(path):
        current = load(path)
        old = current.get(new.subject)
        if old and old.declared_by == "operator" and new.declared_by == "agent":
            raise OperatorOwned(
                f"the operator declared {new.subject}'s quota ({old.state}); an agent "
                f"cannot replace it -- ask the operator"
            )
        current[new.subject] = new
        atomic_write(path, _dump(current.values()))
    return new, old


def forget(subject: str, path: Path | None = None) -> Profile | None:
    """Remove a subject's profile, so it will be asked for again. Returns what was removed."""
    path = path or store_path()
    with locked(path):
        current = load(path)
        old = current.pop(subject, None)
        if old is not None:
            atomic_write(path, _dump(current.values()))
    return old


@dataclass(frozen=True)
class Summary:
    """What `quota list` reports: profiles by state, so an undeclared subject is visible."""

    limited: list[Profile] = field(default_factory=list)
    unlimited: list[Profile] = field(default_factory=list)
    unknown: list[Profile] = field(default_factory=list)


def summary(profiles: dict[str, Profile]) -> Summary:
    s = Summary()
    for p in sorted(profiles.values(), key=lambda p: p.subject):
        getattr(s, p.state).append(p)
    return s
