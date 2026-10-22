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


def _refuse(ev: dict[str, Any], ids: list[str], why: str) -> tuple[str, str]:
    """No rerun could be made: the listed failures have no recorded passing rerun."""
    reason = (
        f"exit 0, but the output lists {_plural(len(ids))} failing ({_names(ids)}) with "
        f"no recorded passing rerun: {why}. NOT a pass."
    )
    ev["rerun"] = {"outcome": "not run", "tests": ids, "note": why}
    ev["reason"] = reason
    return "failed", reason


def _log_flakes(repo: Path, cfg, it, gate: str, gdef, root: Path, ev: dict[str, Any]) -> None:
    """The listed tests that passed alone go to the flake log; a log that cannot be written
    is said in the evidence."""
    flaked = ev["rerun"]["flaked"]
    if not flaked:
        return
    n = FK.record(
        repo, flaked, item=it.id, gate=gate, commit=W.head_sha(root) if root.exists() else "",
        how="the listed failures passed on a rerun", why=ev.get("failed_reasons"),
        tails=ev.get("failure_tails"), cfg=cfg,
    )  # fmt: skip
    if n is None:
        ev["flakes_log_error"] = "the flake log could not be written (.ddflow/local/flakes.jsonl)"
    elif n:
        ev["flakes_logged"] = n


def _note_rerun(ev: dict[str, Any], gdef, cmd: str, ids: list[str], outcome: str, rev) -> bool:
    """Put the rerun in ``ev["rerun"]``; whether every listed test passed alone."""
    still = rev.get("failed_tests") or []
    ok = outcome == "passed" and not still and not rev.get("failed_tests_more")
    kept = not TS.fail_fast(gdef.command)  # a fail-fast rerun stops at its first failure
    passed_alone = [t for t in ids if t not in still] if still and kept else []
    ev["rerun"] = {
        "command": cmd, "tests": ids, "outcome": outcome, "exit": rev.get("exit"),
        "still_failing": still, "failed_tests_more": rev.get("failed_tests_more", 0),
        "tail": rev.get("tail", "")[-800:], "flaked": ids if ok else passed_alone,
    }  # fmt: skip
    return ok


def _verdict(ok: bool, outcome: str, ids: list[str], still: list[str]) -> str:
    if ok:
        return f"passed after rerunning {_plural(len(ids))}: {_names(ids)}, each passed on rerun"
    ran = "failed" if outcome == "failed" or still else "did not run"
    return (
        f"exit 0, but the output lists {_plural(len(ids))} failing and the rerun alone "
        f"{ran} ({_names(still or ids)}). NOT a pass."
    )


def apply(
    repo: Path, cfg, it, gate: str, gdef, result: str, ev: dict[str, Any], rerun
) -> tuple[str, str]:
    """``(outcome, reason)`` for a gate whose command passed: ``result`` and "" when no test
    failed in its output; else the rerun's verdict. Notes the rerun in ``ev["rerun"]``."""
    ids = ev.get("failed_tests") or []
    more = int(ev.get("failed_tests_more") or 0)
    if result != "passed" or not (ids or more):
        return result, ""
    root = W.load_path(repo, it.worktree) if gdef.cwd == "worktree" and it.worktree else repo
    cmd = "" if more else TS.run_command(gdef.command, ids, root)
    if not cmd:
        return _refuse(
            ev, ids,
            f"{more} more failing tests were not named" if more
            else "the gate's command does not run pytest, so they cannot be run alone",
        )  # fmt: skip
    outcome, rev = rerun(replace(gdef, command=cmd))
    ok = _note_rerun(ev, gdef, cmd, ids, outcome, rev)
    _log_flakes(repo, cfg, it, gate, gdef, root, ev)
    reason = _verdict(ok, outcome, ids, ev["rerun"]["still_failing"])
    ev["reason"] = reason
    return ("passed" if ok else "failed"), reason
