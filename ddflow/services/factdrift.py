"""Drift of the one fact record (DRV-facts): expiry, version range, unknown-degrades.

`assess` judges one fact against today and an installed tool version; `effective` demotes
a drifted fact to `unverified` so dependent decisions stop trusting it; `pick` chooses the
best still-believable fact or degrades to a fallback and says why; `doctor` / `report`
list the drifted facts of a table. Scheduling re-probes is `P-agent-parity.drift`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..core import clock
from . import facts

_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}
_CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<)?\s*v?(\d+(?:\.\d+)*)")


def version_tuple(text: str) -> tuple[int, ...]:
    """Leading numeric dotted part of a version ("v1.2.3-beta" -> (1, 2, 3)), trailing
    zeros dropped so 1.4 == 1.4.0. ValueError when there is none."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not m:
        raise ValueError(f"{text!r} is not a version")
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def parse_range(spec: str) -> list[tuple[str, tuple[int, ...]]]:
    """Comma-separated clauses (`>=1.2,<2`); a bare version means `==`."""
    clauses = []
    for part in spec.split(","):
        m = _CLAUSE.match(part.strip())
        if not m or part.strip()[m.end():].strip():
            raise ValueError(f"cannot read range clause {part.strip()!r}")
        clauses.append((m.group(1) or "==", version_tuple(m.group(2))))
    return clauses


def in_range(version: str, spec: str) -> bool:
    v = version_tuple(version)
    return all(_OPS[op](v, bound) for op, bound in parse_range(spec))


@dataclass(frozen=True)
class Assessment:
    state: str  # ok | expired | out_of_range | unknown
    reason: str = ""

    @property
    def drifted(self) -> bool:
        return self.state in ("expired", "out_of_range")


def assess(
    fact: dict[str, Any],
    installed: str | None = None,
    today: date | None = None,
    grace_days: int = 0,
) -> Assessment:
    today = today or clock.now_utc().date()
    exp = fact.get("expires")
    if exp:
        due = clock.parse_date(str(exp))
        if (today - due).days > grace_days:
            return Assessment("expired", f"expired {due.isoformat()}")
    spec = fact.get("versions")
    if spec:
        if not installed:
            return Assessment("unknown", f"installed version unknown, fact holds for {spec}")
        try:
            if not in_range(installed, spec):
                return Assessment("out_of_range", f"installed {installed} is outside {spec}")
        except ValueError as e:
            return Assessment("unknown", str(e))
    return Assessment("ok")


def effective(fact: dict[str, Any], installed: str | None = None, **kw: Any) -> dict[str, Any]:
    """The fact as dependent decisions should read it: a drifted fact becomes `unverified`
    with the reason in its note; the input is not changed. An `unknown` assessment (a range
    but no installed version) is not drift: `pick` degrades on it, this leaves the fact be."""
    a = assess(fact, installed, **kw)
    if not a.drifted:
        return fact
    note = f"{a.state}: {a.reason}"
    if fact.get("note"):
        note += f"; {fact['note']}"
    return {**fact, "s": "unverified", "note": note}


@dataclass(frozen=True)
class Pick:
    value: Any
    fact: dict[str, Any] | None
    reason: str = ""


def pick(
    candidates: list[dict[str, Any]],
    fallback: Any = None,
    installed: str | None = None,
    what: str = "fact",
    **kw: Any,
) -> Pick:
    """Best believable fact (observation outranks document; drifted and unknown-version ones are skipped), else
    `fallback` with a reason naming why nothing was usable."""
    why = []
    live = []
    for f in candidates:
        a = assess(f, installed, **kw)
        if a.state != "ok":
            why.append(f"{a.state} ({a.reason})")
        elif f.get("s") in facts.READABLE or f.get("s") == "none":
            live.append(f)
        else:
            why.append("unverified")
    top = facts.best(live)
    if top is not None:
        return Pick(top.get("v"), top)
    detail = "; ".join(why) if why else "no fact recorded"
    return Pick(fallback, None, f"{what} unknown, using fallback: {detail}")


@dataclass(frozen=True)
class Finding:
    key: str
    state: str
    reason: str


def doctor(
    table: Mapping[str, dict[str, Any]],
    installed: Mapping[str, str] | None = None,
    **kw: Any,
) -> list[Finding]:
    """Drifted facts of a table (key -> fact); `installed` maps key -> installed version."""
    out = []
    for key, f in table.items():
        a = assess(f, (installed or {}).get(key), **kw)
        if a.drifted:
            out.append(Finding(key, a.state, a.reason))
    return out


def report(found: list[Finding]) -> list[str]:
    return [f"{f.key}: {f.state} - {f.reason}" for f in found]
