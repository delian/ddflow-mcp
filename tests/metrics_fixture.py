"""A frozen log with known durations, for the metrics tests: same events, same figures."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold

H = 3600.0
NOW = T0 + 100 * H


def finished(
    log: Log, tid: str, claim: float, hours: float, *, globs: str = "", needs: str = ""
) -> None:
    """A task filed at T0, claimed at ``claim`` and done ``hours`` later, gated by research,
    a failed then passed rubber_duck, and a skipped docs gate."""
    log.task(tid, globs, needs)
    log.claim("a1", tid, claim, globs)
    log.add("gate.started", tid, "a1", claim + 60, gate="research")
    log.add("gate.passed", tid, "a1", claim + 360, gate="research", by="a1", evidence={})
    log.add(
        "gate.failed",
        tid,
        "a1",
        claim + hours * H / 2,
        gate="rubber_duck",
        by="r1",
        evidence={"model": "m-alpha", "elapsed_s": 300},
    )
    log.add(
        "gate.passed",
        tid,
        "a1",
        claim + hours * H - 60,
        gate="rubber_duck",
        by="r1",
        evidence={"model": "m-alpha", "elapsed_s": 200},
    )
    log.add(
        "gate.skipped",
        tid,
        "a1",
        claim + hours * H - 30,
        gate="docs",
        by="a1",
        reason="no user change",
    )
    log.complete("a1", tid, claim + hours * H)
    log.release("a1", tid, claim + hours * H + 1)


def twelve(log: Log | None = None) -> Log:
    """Twelve finished tasks T01..T12 of 1..12 hours each, one after another."""
    log = log or Log()
    t = T0
    for n in range(1, 13):
        finished(log, f"T{n:02d}", t, float(n), globs=f"f{n}.py")
        t += n * H + 60
    return log


def frozen(log: Log, now: float = NOW):
    cfg = Config()
    return fold(list(log.events), strict=False), cfg, now
