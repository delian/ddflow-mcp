"""Keep a full run's result when main moves, and test only what the move reaches
(SV-revalidate-delta; R-suite-lock-principles-2026-11-12).

A full-suite pass was recorded on commit A. Main is now at B. The delta A..B is the paths that
differ, minus ``.ddflow/`` and ``[gates].doc_globs`` documents. Then:

* no path of the delta is in any test's closure (``testselect``): the pass STANDS for B, with the
  note 'unchanged by delta';
* the delta reaches some tests and no hub: only those tests need running (``selected``), and the
  combination is recorded as 'full on A plus selected on delta B'; it is ``partial`` when the
  selection fell back (tests cut by the budget, files the import graph could not read), never a
  silent pass;
* the delta changes a hub or test infrastructure: only a full run is safe (``full``);
* git or the selector cannot say: ``unavailable``, never a pass.

``assess`` reads git through the injected ``selector`` (default ``testselect.select``) over a tree
checked out at B."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import bookkeeping
from ..core.globs import inside
from ..infra import git as G
from . import testselect as TS

STANDS, SELECTED, FULL, UNAVAILABLE = "stands", "selected", "full", "unavailable"
NOTE_STANDS = "unchanged by delta"


@dataclass
class Verdict:
    kind: str
    #: what the revalidated result is: "passed" (stands, or selected tests pass with no fallback),
    #: "partial" (selected, with a fallback), "" when there is no result to keep
    outcome: str = ""
    delta: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    why: str = ""
    note: str = ""


def _delta(repo: Path, a: str, b: str, doc_globs: list[str]) -> list[str] | None:
    paths = G.paths(repo, "diff", "--name-only", a, b)
    if paths is None:
        return None
    return [
        p for p in paths if not bookkeeping.is_state(p) and not any(inside(p, g) for g in doc_globs)
    ]


def assess(
    repo: Path,
    a: str,
    b: str,
    cfg: Any,
    *,
    selector: Callable[..., TS.Selection | None] = TS.select,
) -> Verdict:
    """Whether the pass recorded on ``a`` still holds on ``b`` (``repo`` checked out at ``b``)."""
    if not a or not b:
        return Verdict(UNAVAILABLE, why="the pass or the tree names no commit")
    if a == b:
        return Verdict(STANDS, "passed", note=NOTE_STANDS)
    delta = _delta(repo, a, b, list(cfg.gates.doc_globs or []))
    if delta is None:
        return Verdict(
            UNAVAILABLE, why=f"git could not say what changed between {a[:12]} and {b[:12]}"
        )
    if not delta:
        return Verdict(STANDS, "passed", note=NOTE_STANDS)
    g = cfg.gates
    sel = selector(
        repo,
        a,
        changed=delta,
        max_files=int(g.unit_tests_max_files),
        hub_importers=int(g.unit_tests_hub_importers),
        max_hops=int(g.unit_tests_max_hops),
        name_max_tests=int(g.unit_tests_name_max_tests),
    )
    if sel is None:
        return Verdict(UNAVAILABLE, delta=delta, why="the test selector could not answer")
    if sel.full:
        return Verdict(FULL, delta=delta, why=sel.full)
    tests = [t.path for t in sel.tests]
    if not tests and not sel.cut:
        if sel.unparsed:
            return Verdict(
                FULL, delta=delta, why=f"{len(sel.unparsed)} changed .py file(s) could not be read"
            )
        return Verdict(STANDS, "passed", delta=delta, note=NOTE_STANDS)
    fallback = bool(sel.cut or sel.unparsed)
    return Verdict(
        SELECTED,
        "partial" if fallback else "passed",
        delta=delta,
        tests=tests,
        note=f"full on {a[:12]} plus selected on delta {b[:12]} ({len(tests)} test file(s))",
        why="selection fell back: tests cut or files unread" if fallback else "",
    )


def combined(v: Verdict, selected_passed: bool | None) -> str:
    """The outcome of the combination once the selected tests ran: ``failed`` when they failed,
    ``v.outcome`` when they passed, "" (no result) when they were not run."""
    if v.kind != SELECTED:
        return v.outcome
    if selected_passed is None:
        return ""
    return v.outcome if selected_passed else "failed"
