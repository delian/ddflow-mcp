"""Test-polluter bisect: the operation behind `ddflow bisect` (B26).

The search itself is `services.bisect`; this is input handling (which files are the
candidates, what the command is) and the exit-code contract.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ..services import bisect as B

#: Where the candidates come from when none are named: the usual place for a test suite.
DEFAULT_GLOB = "tests/**/test_*.py"


def _norm(repo: Path, path: str) -> str:
    """`path` as the repo-relative, forward-slash spelling the glob produces, so that
    `./tests/t.py`, `tests/t.py` and `/abs/repo/tests/t.py` are one file."""
    p = PurePath(path)
    if p.is_absolute():
        try:
            p = p.relative_to(repo)
        except ValueError:
            return p.as_posix()
    return PurePath(os.path.normpath(p)).as_posix()


def _collection_key(path: str) -> tuple[str, ...]:
    """Sort key giving pytest's collection order: each directory's entries are sorted by
    name and recursed depth first, so `tests/test_a/` runs before `tests/test_a.py`
    ('test_a' < 'test_a.py'), which a whole-path string sort gets backwards ('.' < '/')."""
    return tuple(path.split("/"))


def candidates_before(repo: Path, victim: str, names: list[str], glob: str) -> list[str]:
    """The files that run before `victim` in a full run, excluding the victim's own file.

    Named files are normalised (repo-relative, forward slashes, `./` and `..` collapsed),
    dropped if they are the victim's file, and kept in the order given. Otherwise `glob` is
    expanded and sorted the way pytest collects (see `_collection_key`), and only the files
    that sort BEFORE the victim's are candidates: a file that runs after cannot pollute it.
    The rule is the same whether or not the glob happens to match the victim's own file.
    """
    victim_file = _norm(repo, victim.split("::", 1)[0])
    if names:
        return [_norm(repo, n) for n in names if _norm(repo, n) != victim_file]
    found = sorted(
        (p.relative_to(repo).as_posix() for p in repo.glob(glob or DEFAULT_GLOB) if p.is_file()),
        key=_collection_key,
    )
    mine = _collection_key(victim_file)
    return [f for f in found if _collection_key(f) < mine]


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
