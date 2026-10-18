"""A gate that fails the way the base branch fails is baseline-red, not the branch's fault
(DX-gate-baseline-red, B23bd81462d).

When `ci` or `unit_tests` fails with test ids read from its output, only those ids are run
again in a scratch worktree of the base. If every one of them fails there too, the failure
is not this change's: the gate is recorded `partial` (a coverage gap `complete` accepts, for
exactly those ids) with the reason ``baseline-red: <ids>, bug <id>``. Any id that passes on
the base, any id the output did not name (a capped list, a fail-fast run) or a command that
does not run pytest leaves the gate FAILED.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...infra import worktree as W
from .. import testselect as TS

GATES = ("ci", "unit_tests")
SHOWN = 5
#: Said in the evidence: the comparison is only as clean as a fresh checkout is.
ISOLATION = (
    "fresh detached worktree of the base; the project's own command ran in it, so an "
    "editable install of the branch or git-ignored fixtures can still mislead it"
)


def candidate_ids(gate: str, command: str, ev: dict[str, Any], fail_fast: bool) -> list[str]:
    """The failing ids to try on the base ([] when the failure cannot be attributed)."""
    if gate not in GATES or fail_fast or ev.get("failed_tests_more"):
        return []
    return list(ev.get("failed_tests") or [])


def on_base(repo: Path, base: str, gdef, ids: list[str], run: Callable[[Any, Path], tuple]) -> dict:
    """Run ``ids`` alone on ``base``: ``{"red": bool, ...}``. ``run(gdef, cwd)`` executes a
    command gate and returns (outcome, evidence)."""
    if not ids:  # all() of nothing is True: no named test is no attribution
        return {"red": False, "why": "no failing test id to run on the base"}
    sha = W.rev(repo, base)
    if not sha:
        return {"red": False, "why": f"base {base} does not resolve"}
    with W.scratch_tree(repo, sha, detach=True, prefix="ddflow-base.", quiet=True) as tree:
        if tree.path is None:
            return {"red": False, "why": "no scratch worktree of the base could be made"}
        cmd = TS.run_command(gdef.command, ids, tree.path)
        if not cmd:
            return {"red": False, "why": "the gate's command does not run pytest"}
        outcome, ev = run(replace(gdef, command=cmd), tree.path)
    failing = ev.get("failed_tests") or []
    red = outcome == "failed" and all(i in failing for i in ids)
    return {"red": red, "isolation": ISOLATION, "base": base, "base_sha": sha, "outcome": outcome, "command": cmd,
            "ids": ids, "still_failing": failing}  # fmt: skip


def bug_id(ids: list[str], sha: str) -> str:
    return "B" + D.of_obj([sorted(ids), sha[:12]], length=10)


def reason_text(ids: list[str], bug: str) -> str:
    shown = ", ".join(ids[:SHOWN]) + (f" (+{len(ids) - SHOWN} more)" if len(ids) > SHOWN else "")
    return f"baseline-red: {shown}, bug {bug or 'not filed'}"
