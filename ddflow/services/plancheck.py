"""Mechanical checks on a PROPOSED plan -- before anything is filed (B-gap-plan-lint).

Decomposition is a prompt-quality problem and stays with the agent. What CAN be checked
mechanically is checked here, and every finding names the fix:

* a wildcard in a DIRECTORY position (`ddflow/tests/*`) hides which files a task touches,
  so the overlap test that keeps two agents off one file cannot tell two tasks apart;
* a `needs` naming an id the plan does not have -- a typo that reads as a satisfied
  dependency, so the task starts too early and nothing says so;
* a dependency CYCLE: nothing on it can ever start;
* two tasks that can run TOGETHER (neither reaches the other through `needs`) whose globs
  overlap: they will collide, and one of them should wait -- the same re-order the claim
  conflict report asks for;
* a task that never says what finishing means (`acceptance`), and one whose globs span more
  top-level directories than a single task should (`max_dirs`) -- a candidate for splitting.

Pure: it reads the plan it is given. `tracked` (the repository's files) and `similar` (the
queue's duplicate check) are passed IN, so this module does no I/O and its tests need no
repository, no log and no clock.

A plan is ``{id: item}``; an item carries ``needs``, ``globs``, ``kind`` (default "task")
and, for a task, ``acceptance``. The plan's WAVES come from `core.schedule.waves`, which
owns that computation for the queue as well (`B-dag-waves-fanout` reuses it).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.globs import inside, overlap
from ..core.graph import find_cycles
from ..core.schedule import waves


@dataclass(frozen=True)
class Finding:
    """One thing to fix before the plan is filed. ``ids`` are the plan's own ids."""

    kind: str  #: wide_glob | unknown_dep | cycle | overlap | no_acceptance | wide_span | duplicate
    ids: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ids": list(self.ids), "detail": self.detail}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    #: The wave each item would start in (1 = nothing holds it back), from `waves`.
    waves: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.as_dict() for f in self.findings],
            "waves": dict(sorted(self.waves.items(), key=lambda kv: (kv[1], kv[0]))),
        }


#: A wildcard in one of these positions hides the FILES a task touches, not just which of
#: them it names: `src/*/cli.py` could be any of a dozen files under src.
_WILDCARDS = ("*", "?", "[")

#: A glob matching more tracked files than this is wider than it reads -- a dump, a generated
#: directory, or a pattern that means "everything": not the handful of files a task should
#: name. (A knob once `P-configurable` reaches this module.)
WIDE_MATCH = 50


def wildcard_dirs(globs: Iterable[str]) -> list[str]:
    """The globs with a wildcard in a DIRECTORY position (a bare `*` counts: all of it)."""
    out = []
    for g in globs:
        parts = g.split("/")
        if len(parts) == 1 and parts[0] in _WILDCARDS:
            out.append(g)
            continue
        if any(any(w in part for w in _WILDCARDS) for part in parts[:-1]):
            out.append(g)
    return out


def _needs(item: Mapping[str, Any]) -> list[str]:
    return [str(n) for n in item.get("needs", [])]


def _globs(item: Mapping[str, Any]) -> list[str]:
    return [str(g) for g in item.get("globs", [])]


def reaches(plan: Mapping[str, Any], start: str, target: str) -> bool:
    """Does ``start`` wait (transitively) on ``target``? Within the plan's own items."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        here = stack.pop()
        for dep in _needs(plan.get(here, {})):
            if dep == target:
                return True
            if dep in plan and dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return False


def wide_globs(plan: Mapping[str, Any]) -> list[Finding]:
    return [
        Finding(
            "wide_glob",
            (iid,),
            f"{g!r} has a wildcard in a directory position: which files it touches cannot "
            f"be told, so a task filed with it cannot be checked against another's",
        )
        for iid in sorted(plan)
        for g in wildcard_dirs(_globs(plan[iid]))
    ]


def unknown_deps(plan: Mapping[str, Any], known: Iterable[str] | None = ()) -> list[Finding]:
    """A `needs` that is neither in the plan nor in ``known`` (the queue's own ids).

    ``known`` None means the queue could not be read: a `needs` outside the plan then cannot
    be told from a queued id, so it is not reported.
    """
    if known is None:
        return []
    outside = set(known)
    return [
        Finding(
            "unknown_dep",
            (iid, dep),
            f"{iid} needs {dep!r}, which the plan does not have: a typo here reads as a "
            f"satisfied dependency",
        )
        for iid in sorted(plan)
        for dep in _needs(plan[iid])
        if dep not in plan and dep not in outside
    ]


def cycles(plan: Mapping[str, Any]) -> list[Finding]:
    """Dependency cycles, each reported once (the walk that finds them is `find_cycles`)."""
    return [
        Finding(
            "cycle",
            tuple(cycle),
            f"nothing on this cycle can start: {' -> '.join(cycle)} -> {cycle[0]}",
        )
        for cycle in find_cycles(plan, lambda it: list(_needs(it)))
    ]


def overlaps(plan: Mapping[str, Any], *, tracked: Iterable[str] | None = None) -> list[Finding]:
    """Pairs that can run together AND would edit one file.

    "Can run together" is the plan's own dependency graph: one reaches the other, and the
    order is real rather than a coincidence to re-order. The globs are compared with the
    same `overlap` the conflict detector refuses a lease for, so this reports what `claim`
    would refuse -- read a plan, not guess at one.
    """
    out: list[Finding] = []
    for ida in sorted(plan):
        for idb in sorted(plan):
            if ida >= idb or reaches(plan, ida, idb) or reaches(plan, idb, ida):
                continue
            for ga in _globs(plan[ida]):
                for gb in _globs(plan[idb]):
                    if overlap(ga, gb):
                        out.append(
                            Finding(
                                "overlap",
                                (ida, idb),
                                f"{ida} and {idb} can run together and both touch "
                                f"{ga!r} / {gb!r}: one of them should need the other, or "
                                f"their globs should be narrowed",
                            )
                        )
    if tracked is not None:
        files = list(tracked)
        out += [
            Finding(
                "wide_glob",
                (iid,),
                f"{g!r} matches {n} tracked file(s): wider than it looks, so it collides "
                f"with more than it reads as touching",
            )
            for iid in sorted(plan)
            for g in _globs(plan[iid])
            if (n := sum(1 for f in files if inside(f, g))) > WIDE_MATCH
        ]
    return out


def says_acceptance(item: Mapping[str, Any]) -> bool:
    """Does the item say how it is known to be done?

    An `acceptance` FIELD, or an `ACCEPTANCE:` line in the body. Both are real: the tasks
    filed in this queue state theirs in the body (that is where an operator writes it), so a
    plan read back from the queue must not be told it has none.
    """
    if str(item.get("acceptance", "")).strip():
        return True
    return any(
        line.strip().lower().startswith("acceptance")
        for line in str(item.get("body", "")).splitlines()
    )


def missing_acceptance(plan: Mapping[str, Any]) -> list[Finding]:
    """A task must say what finishing MEANS, or the gate pipeline has nothing to check."""
    return [
        Finding(
            "no_acceptance",
            (iid,),
            f"{iid} says nothing about how it is known to be done: add acceptance to the plan",
        )
        for iid in sorted(plan)
        if plan[iid].get("kind", "task") == "task" and not says_acceptance(plan[iid])
    ]


def wide_spans(plan: Mapping[str, Any], *, max_dirs: int = 3) -> list[Finding]:
    """Globs spanning many top-level directories: usually two tasks wearing one id."""
    out = []
    for iid in sorted(plan):
        tops = {g.split("/")[0] for g in _globs(plan[iid]) if "/" in g}
        if len(tops) > max_dirs:
            out.append(
                Finding(
                    "wide_span",
                    (iid,),
                    f"{iid} declares files under {len(tops)} top-level directories "
                    f"({', '.join(sorted(tops))}): a candidate for splitting",
                )
            )
    return out


def duplicates(
    plan: Mapping[str, Any], similar: Callable[[str, str], list[str]] | None
) -> list[Finding]:
    """Against the EXISTING queue, through the add-time check, when a caller can provide it."""
    if similar is None:
        return []
    return [
        Finding(
            "duplicate",
            (iid, *others),
            f"{iid} reads like {', '.join(others)}, already in the queue",
        )
        for iid in sorted(plan)
        if (others := similar(str(plan[iid].get("title", "")), str(plan[iid].get("body", ""))))
    ]


def check(
    plan: Mapping[str, Any],
    *,
    tracked: Iterable[str] | None = None,
    similar: Callable[[str, str], list[str]] | None = None,
    known: Iterable[str] | None = (),
    cap: int | None = None,
    max_dirs: int = 3,
) -> Report:
    """Every check above, plus the waves the plan would run in.

    The waves are computed even when there are findings: a plan with one wide glob is still
    worth seeing laid out, and the two answers are read together. ``known`` is the ids a
    `needs` may name besides the plan's own: a plan filed into a queue depends on what is
    already there.
    """
    report = Report(
        findings=[
            *wide_globs(plan),
            *unknown_deps(plan, known),
            *cycles(plan),
            *overlaps(plan, tracked=tracked),
            *missing_acceptance(plan),
            *wide_spans(plan, max_dirs=max_dirs),
            *duplicates(plan, similar),
        ],
        waves=waves(plan, edges=_needs, globs_of=_globs, cap=cap),
    )
    report.findings.sort(key=lambda f: (f.kind, f.ids, f.detail))
    return report
