"""Scoring for the shared evaluation harness (SE-eval-harness): planted faults, miss rate, and
the rule that a workflow is enabled by default only after it catches its planted set
(D-shared-engines-r2 6).

A fixture record (`templates/kinds/evalfixture.toml`) carries its planted faults as
``id=word|word`` entries. A workflow's findings are plain strings; a fault is *caught* when a
finding contains any of its words (case-insensitive), *missed* otherwise, and a finding that
matches no fault is *extra* (on a clean fixture, a false positive).

Pure: no I/O, no clock. Every number (the miss rate a workflow may have, the fixtures it needs) is a
required parameter: its default is a config knob, added with the surface (SE-eval-harness.3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class FaultSpecError(ValueError):
    """A planted entry that is not ``id=word|word``."""


@dataclass(frozen=True)
class Fault:
    id: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class Score:
    """One fixture's outcome."""

    fixture: str
    workflow: str
    caught: tuple[str, ...]
    missed: tuple[str, ...]
    extra: tuple[str, ...]

    @property
    def planted(self) -> int:
        return len(self.caught) + len(self.missed)


def parse_fault(entry: str) -> Fault:
    ident, sep, words = entry.partition("=")
    found = tuple(w.strip().lower() for w in words.split("|") if w.strip())
    if not sep or not ident.strip() or not found:
        raise FaultSpecError(f"a planted fault is 'id=word|word'; got {entry!r}")
    return Fault(ident.strip(), found)


def parse_faults(entries: Iterable[str]) -> list[Fault]:
    faults = [parse_fault(e) for e in entries]
    seen: set[str] = set()
    for f in faults:
        if f.id in seen:
            raise FaultSpecError(f"planted fault {f.id!r} is listed twice")
        seen.add(f.id)
    return faults


def score(fixture: Mapping[str, Any], findings: Iterable[str]) -> Score:
    """Score ``findings`` against a fixture record's planted faults."""
    faults = parse_faults(fixture.get("planted") or ())
    texts = [f.lower() for f in findings]
    caught, matched = [], set()
    for fault in faults:
        hits = [i for i, t in enumerate(texts) if any(w in t for w in fault.words)]
        if hits:
            caught.append(fault.id)
            matched.update(hits)
    missed = [f.id for f in faults if f.id not in caught]
    extra = [t for i, t in enumerate(texts) if i not in matched]
    return Score(
        str(fixture.get("id", "")), str(fixture.get("workflow", "")),
        tuple(caught), tuple(missed), tuple(extra),
    )  # fmt: skip


def miss_rate(scores: Iterable[Score]) -> float | None:
    """Missed faults over planted faults; None when nothing was planted."""
    rows = list(scores)
    planted = sum(s.planted for s in rows)
    return sum(len(s.missed) for s in rows) / planted if planted else None


def report(
    scores: Iterable[Score],
    *,
    max_miss_rate: float,
    min_fixtures: int,
) -> dict[str, dict[str, Any]]:
    """Per workflow: fixtures, planted, missed, miss_rate, false_positives, and whether it
    may be enabled by default (`enable_by_default`) with the reason."""
    by: dict[str, list[Score]] = {}
    for s in scores:
        by.setdefault(s.workflow, []).append(s)
    out: dict[str, dict[str, Any]] = {}
    for wf, rows in sorted(by.items()):
        row: dict[str, Any] = {
            "fixtures": len(rows),
            "planted_fixtures": sum(1 for s in rows if s.planted),
            "planted": sum(s.planted for s in rows),
            "missed": sum(len(s.missed) for s in rows),
            "miss_rate": miss_rate(rows),
            "false_positives": sum(len(s.extra) for s in rows),
        }
        row["enabled"], row["why"] = enable_by_default(
            row, max_miss_rate=max_miss_rate, min_fixtures=min_fixtures
        )
        out[wf] = row
    return out


def enable_by_default(
    row: Mapping[str, Any] | None,
    *,
    max_miss_rate: float,
    min_fixtures: int,
) -> tuple[bool, str]:
    """May a workflow be enabled by default, given its `report` row? ``(ok, reason)``."""
    if not row:
        return False, "no fixture was run for it, so nothing was measured"
    have = row["planted_fixtures"]
    if have < min_fixtures:
        return False, f"needs at least {min_fixtures} planted-fault fixture(s); has {have}"
    rate = row["miss_rate"]
    if rate is None:
        return False, "no fault is planted in its fixtures, so nothing was measured"
    if rate > max_miss_rate:
        return (
            False,
            f"missed {row['missed']} of {row['planted']} planted faults ({rate:.0%} > {max_miss_rate:.0%})",
        )
    return True, f"caught its planted set ({row['planted']} faults in {row['fixtures']} fixtures)"
