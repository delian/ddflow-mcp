"""`ddflow health`: the watchdog's view, and its periodic pass (DX-health-watch).

``health()`` inspects every lane and reports; ``health(run=True)`` is the pass a scheduler,
loop or hook invokes: it also makes the corrections `[health].mode` allows and records the
run for `ddflow cadence`. Corrections never kill a process or delete work
(`services.healthwatch`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import healthwatch as HW
from ._base import _load
from .reporting.waitcycles import wait_cycles


def summary(findings: list[Any]) -> str:
    """One line for `status`/`brief`: '' when nothing is wrong."""
    if not findings:
        return ""
    by: dict[str, int] = {}
    for f in findings:
        by[f.kind] = by.get(f.kind, 0) + 1
    return "health: " + ", ".join(f"{n} {k}" for k, n in sorted(by.items()))


def findings_for(log, st, cfg, repo: Path, now: float | None = None, activity=None):
    """(findings, cycles) as of ``now`` -- what `doctor` and `status` read."""
    at = time.time() if now is None else now
    cycles = wait_cycles(st, cfg, at)
    return HW.inspect(log, st, cfg, repo, at, cycles, activity=activity), cycles


def render(mode: str, findings: list[Any], moves: list[dict[str, Any]]) -> str:
    if not findings:
        return f"All lanes healthy (mode {mode})."
    lines = [f"{len(findings)} finding(s) (mode {mode}):"]
    lines += [f"  {f.render()}" for f in findings]
    lines += [f"  -> {m['line']}" for m in moves]
    return "\n".join(lines)


def health(
    repo: Path,
    *,
    run: bool = False,
    mode: str = "",
    agent: str = "",
    now: float | None = None,
    activity=None,
) -> O.Outcome:
    """Inspect; with ``run`` also correct per ``mode`` (default `[health].mode`) and record it."""
    log, cfg, st = _load(repo, agent)
    mode = mode or cfg.health.mode
    if mode not in HW.MODES:
        return O.failed("health", f"unknown mode {mode!r}: one of {', '.join(HW.MODES)}")
    at = time.time() if now is None else now
    found, cycles = findings_for(log, st, cfg, repo, at, activity)
    moves: list[dict[str, Any]] = []
    if run and mode != "off":
        moves = HW.correct(log, st, cfg, found, cycles, mode, at, activity=activity)
        HW.record_run(log, mode, len(found), moves)
    return O.ok(
        "health",
        mode=mode,
        ran=run and mode != "off",
        findings=[f.as_dict() for f in found],
        moves=moves,
        count=len(found),
        text=render(mode, found, moves),
    )
