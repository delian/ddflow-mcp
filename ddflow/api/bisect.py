"""Test-polluter bisect: the operation behind `ddflow bisect` (B26).

The search itself is `services.bisect`; this is input handling (which files are the
candidates, what the command is) and the exit-code contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ..services import bisect as B

#: Where the candidates come from when none are named: the usual place for a test suite.
DEFAULT_GLOB = "tests/**/test_*.py"


def candidates_before(repo: Path, victim: str, names: list[str], glob: str) -> list[str]:
    """The files that run before `victim` in a full run, excluding the victim's own file.

    Named files are taken exactly as given and in the given order. Otherwise `glob` is
    expanded and sorted, which is how a default pytest run orders files, and only the files
    that sort BEFORE the victim's are candidates: a file that runs after cannot pollute it.
    """
    victim_file = victim.split("::", 1)[0]
    if names:
        return [n for n in names if n != victim_file]
    found = sorted(str(p.relative_to(repo)) for p in repo.glob(glob or DEFAULT_GLOB) if p.is_file())
    if victim_file in found:
        found = found[: found.index(victim_file)]
    return [f for f in found if f != victim_file]


def bisect(
    repo: Path,
    victim: str,
    cmd: str,
    *,
    candidates: str = "",
    glob: str = "",
    timeout_s: float = 600,
    repeat: int = 1,
    max_runs: int = 200,
    agent: str = "",
) -> O.Outcome:
    """Find which earlier test file makes `victim` fail only after it.

    `cmd` runs a list of tests and exits non-zero on failure; ``{tests}`` marks where the
    list goes. Exit 0: a polluter set was found. Exit 2: nothing to report (the victim
    fails alone, it does not fail after the candidates, a run could not be made, or the
    budget ran out) -- the reason says which, and none of them is "no polluter".
    Exit 1: the request itself is unusable (no victim, a command with no placeholder).
    """
    if not victim.strip():
        return O.failed("bisect", "name the victim: the test that fails only in full-suite order")
    runs: list[B.Run] = []
    try:
        probe = B.command_probe(cmd, repo, timeout_s=timeout_s, repeat=repeat, runs=runs)
    except ValueError as exc:
        return O.failed("bisect", str(exc))
    cands = candidates_before(repo, victim, csv_list(candidates), glob)
    result = B.bisect(victim, cands, probe, max_runs=max_runs, runs=runs)
    data: dict[str, Any] = {
        "state": result.state,
        "victim": result.victim,
        "polluters": result.polluters,
        "candidates": result.candidates,
        "summary": result.reason,
        "runs": [
            {
                "tests": len(r.tests),
                "outcome": {True: "passed", False: "failed", None: "could_not_run"}[r.outcome],
                "seconds": r.seconds,
                "detail": r.detail,
            }
            for r in result.runs
        ],
    }
    if result.state == "found":
        return O.ok("bisect", **data)
    return O.nothing("bisect", result.reason, **data)
