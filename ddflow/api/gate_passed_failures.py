"""`gate run`: a command that EXITED 0 yet printed failing tests is not a plain pass
(B-ci-gate-prints-passed-with-failing-tests).

A wrapper that retries, or masks, a red suite exits 0 over output that lists failures. The
gate cannot take its word, so it runs the named tests alone itself. Every one passed alone:
`passed`, and the reason says which tests were rerun. Anything else -- one failed alone,
the list was capped, the tests cannot be run alone -- is never `passed`: a failing test with
no recorded passing rerun cannot yield a pass.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..infra import worktree as W
from ..services import flakes as FK
from ..services import testselect as TS

SHOWN = 8


def _names(ids: list[str]) -> str:
    shown = ", ".join(ids[:SHOWN]) + (f" (+{len(ids) - SHOWN} more)" if len(ids) > SHOWN else "")
    return shown


def _plural(n: int) -> str:
    return f"{n} test" + ("" if n == 1 else "s")


def apply(
    repo: Path, cfg, it, gate: str, gdef, result: str, ev: dict[str, Any], rerun
) -> tuple[str, str]:
    """``(outcome, reason)`` for a gate whose command passed: ``result`` and "" when no test
    failed in its output; else the rerun's verdict. Notes the rerun in ``ev["rerun"]``."""
    from .gates import _fail_fast  # late: gates imports this module through gate_baseline

    ids = ev.get("failed_tests") or []
    more = int(ev.get("failed_tests_more") or 0)
    if result != "passed" or not (ids or more):
        return result, ""
    root = W.load_path(repo, it.worktree) if gdef.cwd == "worktree" and it.worktree else repo
    cmd = "" if more else TS.run_command(gdef.command, ids, root)
    if not cmd:
        why = (
            f"{more} more failing tests were not named" if more
            else "the gate's command does not run pytest, so they cannot be run alone"
        )  # fmt: skip
        reason = (
            f"exit 0, but the output lists {_plural(len(ids))} failing ({_names(ids)}) with "
            f"no recorded passing rerun: {why}. NOT a pass."
        )
        ev["rerun"] = {"outcome": "not run", "tests": ids, "note": why}
        ev["reason"] = reason
        return "failed", reason
    outcome, rev = rerun(replace(gdef, command=cmd))
    still = rev.get("failed_tests") or []
    ok = outcome == "passed" and not still and not rev.get("failed_tests_more")
    ev["rerun"] = {
        "command": cmd, "tests": ids, "outcome": outcome, "exit": rev.get("exit"),
        "still_failing": still, "tail": rev.get("tail", "")[-800:],
        "flaked": ids if ok else [t for t in ids if t not in still] if outcome == "failed" and still and not _fail_fast(gdef.command) else [],
    }  # fmt: skip
    flaked = ev["rerun"]["flaked"]
    n = FK.record(
        repo, flaked, item=it.id, gate=gate, commit=W.head_sha(root) if root.exists() else "",
        how="the listed failures passed on a rerun", why=ev.get("failed_reasons"),
        tails=ev.get("failure_tails"), cfg=cfg,
    ) if flaked else 0  # fmt: skip
    if n:
        ev["flakes_logged"] = n
    if ok:
        reason = f"passed after rerunning {_plural(len(ids))}: {_names(ids)}, each passed on rerun"
        ev["reason"] = reason
        return "passed", reason
    bad = still or ids
    reason = (
        f"exit 0, but the output lists {_plural(len(ids))} failing and the rerun alone "
        f"{'failed' if outcome == 'failed' or still else 'did not run'} ({_names(bad)}). NOT a pass."
    )
    ev["reason"] = reason
    return "failed", reason
