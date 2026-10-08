"""Usage quotas: what each agent or LLM may spend, per window (D-quotas).

A quota belongs to whoever PAYS for the tokens -- a Claude account, an API key, a
self-hosted endpoint -- not to a project, so profiles live in one user-level file and
every ddflow project on this machine reads the same one. Committing them to a project's
log would hand one account's limits to every clone and let two projects disagree about
one account.

A SUBJECT is `agent:<harness>/<account>` (a coding agent logged in to a plan) or
`llm:<base_url>` (a model endpoint). `<account>` is any label without spaces -- `local`
for an agent with no account at all. Where it would be a real account id, use
:func:`account_tag` so the subject never carries the id itself. Its PROFILE is exactly
one of:

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

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.digest import content_digest
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
    return content_digest(account_id, length=12)


def _now() -> str:
    return clock.now_iso(timespec="seconds")


def _parse_time(text: str) -> str:
    try:
        # Refused without a zone: the same wall-clock time is a different moment on
        # another machine.
        t = clock.parse_ts(text, naive="refuse")
    except clock.NoTimezone as exc:
        raise QuotaError(f"anchor {text!r} has no timezone: add Z or an offset") from exc
    except ValueError as exc:
        raise QuotaError(f"anchor {text!r} is not an ISO-8601 time") from exc
    return t.isoformat()


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
        if w.reset == "fixed" and w.unit == "percent":
            raise QuotaError("a percent window resets when the vendor says: it takes no anchor")
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


_WINDOW_FIELDS = frozenset(f.name for f in fields(Window))


def _from_dict(d: dict[str, Any]) -> Profile:
    return Profile(
        subject=d["subject"],
        unlimited=bool(d.get("unlimited", False)),
        unknown=bool(d.get("unknown", False)),
        windows=tuple(
            Window(**{k: v for k, v in w.items() if k in _WINDOW_FIELDS})
            for w in d.get("windows", [])
        ),
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
        version = doc.get("version")
        # A NEWER store is read for the fields this version knows (D-compat): the rest is
        # kept when this version writes. Refusing it would make a newer ddflow's quota
        # file break every older one; only a version that is no version at all is refused.
        if not isinstance(version, int) or isinstance(version, bool) or version < STORE_VERSION:
            raise QuotaError(f"{path}: unsupported version {version!r}")
        out = {}
        for key, d in doc["profiles"].items():
            p = validate(_from_dict(d))
            if p.subject != key:
                # Looked up by key, reported by subject: a mismatch hides the real
                # subject's quota, which is the silent "no quota" this refuses.
                raise QuotaError(f"{path}: entry {key!r} holds subject {p.subject!r}")
            out[key] = p
        return out
    except QuotaError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise QuotaError(
            f"{path} is not a readable quota store ({exc}); fix or remove it -- it is not "
            f"read as 'no quotas', because that would lift every limit silently"
        ) from exc


def _on_disk(path: Path) -> dict[str, Any]:
    """The store as it is on disk, for what a newer ddflow put there; {} when absent or
    unreadable (`load` has already refused an unreadable one before any write)."""
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _with_window_extras(windows: Any, prev: Any) -> Any:
    """``windows`` (this version's) with each window's keys it does not know carried over
    from the on-disk window of the same ``(window, unit)``."""
    if not isinstance(windows, list) or not isinstance(prev, list):
        return windows
    by = {(w.get("window"), w.get("unit")): w for w in prev if isinstance(w, dict)}
    out = []
    for w in windows:
        old = by.get((w.get("window"), w.get("unit"))) if isinstance(w, dict) else None
        out.append({**{k: v for k, v in old.items() if k not in w}, **w} if old else w)
    return out


def _dump(profiles: Iterable[Profile], path: Path | None = None) -> str:
    """The store text. With ``path``, keys this version does not know -- at the top and in
    each profile -- and a newer ``version`` are carried over from what is on disk."""
    old = _on_disk(path) if path is not None else {}
    old_profiles = old.get("profiles") if isinstance(old.get("profiles"), dict) else {}
    out: dict[str, Any] = {}
    for p in sorted(profiles, key=lambda p: p.subject):
        known = {k: v for k, v in p.to_dict().items() if k != "state"}
        prev = old_profiles.get(p.subject)
        extra = (
            {k: v for k, v in prev.items() if k not in known and k != "state"}
            if isinstance(prev, dict)
            else {}
        )
        out[p.subject] = {**extra, **known}
        if isinstance(prev, dict):
            out[p.subject]["windows"] = _with_window_extras(
                known.get("windows"), prev.get("windows")
            )
    version = old.get("version")
    doc = {
        **{k: v for k, v in old.items() if k not in ("version", "profiles")},
        "version": version
        if isinstance(version, int) and version > STORE_VERSION
        else STORE_VERSION,
        "profiles": out,
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
        atomic_write(path, _dump(current.values(), path))
    return new, old


def forget(subject: str, by: str = "agent", path: Path | None = None) -> Profile | None:
    """Remove a subject's profile, so it will be asked for again. Returns what was removed.

    An agent may not forget what the operator declared: forget-then-declare would
    otherwise be a side door around the rule `declare` enforces.
    """
    if by not in DECLARERS:
        raise QuotaError(f"by must be one of {', '.join(DECLARERS)}")
    path = path or store_path()
    with locked(path):
        current = load(path)
        old = current.get(subject)
        if old is not None and old.declared_by == "operator" and by == "agent":
            raise OperatorOwned(
                f"the operator declared {subject}'s quota ({old.state}); an agent cannot "
                f"forget it -- ask the operator"
            )
        if old is not None:
            del current[subject]
            atomic_write(path, _dump(current.values(), path))
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
