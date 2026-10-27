"""Run a bug's ``--repro-cmd`` at filing, in a scratch checkout of the recorded sha.

The reproduction bundle stores the command stamped "given, not executed" (the bundle
task); this module is the execution the verify item adds: `check` runs the command in a
throwaway checkout of the sha recorded with the bug (`infra.worktree.scratch_tree`, the
same machinery the regression verify reuses), the caller decides how many runs the
reading needs (`[bugs] repro_runs`), and the record the bug stores says only what the
runs answered:

    "executed": true
    "at": the sha the checkout ran,
    "reproduced": "yes" when at least one run exited non-zero,
    "runs": how many runs reached an exit code,
    "failures": how many of those exited non-zero,
    "load": the one-minute load when the window opened,
    "output": the tail of the runs' merged output (`[bugs] repro_tail_bytes`),
    "unavailable": why nothing reached an exit code.

A bug whose repro did not fail is UNCONFIRMED until a sighting fails (the fold keeps
"yes" once a run places it). Nothing is read as reproduced without a run behind it: a
window that produced no exit code is unavailable, never reproduced. The command is split
with ``shlex`` and run through the ``infra.proc`` capture call, not the host shell.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from ..infra import proc as P
from ..infra import worktree as W


def check(
    repo: Path, sha: str, cmd: str, runs: int, timeout_s: float, tail_bytes: int
) -> dict[str, Any]:
    """The record the caller stores for ``cmd`` executed at ``sha`` up to ``runs`` times.

    ``runs`` below 1 runs it once; a run cut off (timeout, could not start, no such
    program) never yields an exit code, so a verdict can never cite it. All runs happen
    in ONE scratch checkout of ``sha``, so the command sees the recorded commit, not the
    filer's uncommitted edits; no ``sha`` (git could not name one) is ``unavailable``,
    never a run somewhere else."""
    count = max(1, int(runs))
    try:
        words = shlex.split(cmd or "")
    except ValueError as exc:
        return {"unavailable": f"repro command could not be split: {exc}"}
    if not words:
        return {"unavailable": "repro command is empty"}
    clean = str(sha or "").strip()
    if not clean:
        return {"unavailable": "the tree sha the repro would run at is not recorded"}
    load_start = _first_load()
    with W.scratch_tree(repo, clean, detach=True, prefix="ddflow-repro-", quiet=True) as s:
        if s.path is None:
            return {
                "unavailable": f"no scratch checkout of {clean[:12]}: {s.error}",
                "load": load_start,
            }
        codes: list[int] = []
        out: list[str] = []
        for _ in range(count):
            shot = P.capture(words, timeout=float(timeout_s), cwd=s.path)
            if shot.error is not None or shot.returncode is None:
                continue
            codes.append(int(shot.returncode))
            part = f"{shot.stdout or ''}{shot.stderr or ''}"
            if part:
                out.append(part)
    record: dict[str, Any] = {"executed": bool(codes)}
    if not codes:
        record.update({"unavailable": "no run reached an exit code to read", "load": load_start})
        return record
    failed = [c for c in codes if c != 0]
    keep = max(0, int(tail_bytes))
    text = "\n".join(out)
    record = {
        "executed": True,
        "at": clean,
        "reproduced": "yes" if failed else "no",
        "runs": len(codes),
        "failures": len(failed),
        "load": load_start,
    }
    if text and keep:
        record["output"] = text[-keep:]
        if len(text) > keep:
            record["output_truncated"] = True
    return record


def _first_load() -> str:
    """The load the conditions record wants: the one-minute number, 'unknown' when not had."""
    try:
        return f"{os.getloadavg()[0]:.2f}"
    except (OSError, AttributeError):
        return "unknown"
