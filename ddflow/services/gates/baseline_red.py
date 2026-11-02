"""A gate that fails the way the base branch fails is baseline-red, not the branch's fault
(DX-gate-baseline-red, B23bd81462d).

When `ci` or `unit_tests` fails with test ids read from its output, only those ids are run
again in a scratch worktree of the base. If every one of them fails there too, the failure
is not this change's: the gate is recorded `partial` (which `complete` accepts only once a person
has accepted it, `services.gate_accept`) with the reason ``baseline-red: <ids>, bug <id>``. Any id that passes on
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
from .baseline import cache_load, cache_store

GATES = ("ci", "unit_tests")
SHOWN = 5
#: Ids run on the base at once: under `flakes.MAX_FAILED_TESTS`, which cuts a run's listed failures.
CHUNK = 40
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


def on_base(
    repo: Path, base: str, gdef, ids: list[str], run: Callable[[Any, Path], tuple], ttl_s: int = 0
) -> dict:
    """Run ``ids`` alone on ``base``: ``{"red": bool, ...}``. ``run(gdef, cwd)`` executes a
    command gate and returns (outcome, evidence). With ``ttl_s`` > 0 what was measured on this
    base sha is kept (`services.gates.baseline`): ids already measured are not run again."""
    if not ids:  # all() of nothing is True: no named test is no attribution
        return {"red": False, "why": "no failing test id to run on the base"}
    sha = W.rev(repo, base)
    if not sha:
        return {"red": False, "why": f"base {base} does not resolve"}
    known = cache_load(repo, sha, ttl_s, None, gdef.command)
    if all(i in known for i in ids):
        return {"red": all(known[i] for i in ids), "cached": True, "base": base, "base_sha": sha,
                "ids": ids, "still_failing": [i for i in ids if known[i]]}  # fmt: skip
    todo = [i for i in ids if i not in known]
    outcomes: list[str] = []
    cmd = ""
    with W.scratch_tree(repo, sha, detach=True, prefix="ddflow-base.", quiet=True) as tree:
        if tree.path is None:
            return {"red": False, "why": "no scratch worktree of the base could be made"}
        # Chunks stay under the evidence cap on listed failures, so a run's list is never cut.
        for start in range(0, len(todo), CHUNK):
            part = todo[start : start + CHUNK]
            cmd = TS.run_command(gdef.command, part, tree.path)
            if not cmd:
                return {"red": False, "why": "the gate's command does not run pytest"}
            outcome, seen = _chunk(replace(gdef, command=cmd), tree.path, part, run)
            outcomes.append(outcome)
            if seen is not None:
                known.update(seen)
                cache_store(repo, sha, seen, ttl_s, gdef.command)
    failing = [i for i in ids if known.get(i)]
    red = all(known.get(i) for i in ids)
    return {"red": red, "isolation": ISOLATION, "base": base, "base_sha": sha,
            "outcome": "failed" if red else (outcomes[-1] if outcomes else ""), "command": cmd,
            "ids": ids, "still_failing": failing}  # fmt: skip


def _chunk(gdef, cwd: Path, part: list[str], run) -> tuple[str, dict[str, bool] | None]:
    """``(outcome, {id: failed on the base})`` of one chunk; None for the second when the run
    cannot say which ids PASS: it could not run, listed nothing, listed a cut list, or stopped
    at the first failure (`-x`)."""
    outcome, ev = run(gdef, cwd)
    failing = set(ev.get("failed_tests") or ())
    whole = outcome == "passed" or (
        outcome == "failed" and failing and not TS.fail_fast(gdef.command)
    )
    if not whole or ev.get("failed_tests_more"):
        return outcome, None
    return outcome, {i: i in failing for i in part}


def bug_id(ids: list[str], sha: str) -> str:
    return "B" + D.of_obj([sorted(ids), sha[:12]], length=10)


def reason_text(ids: list[str], bug: str) -> str:
    shown = ", ".join(ids[:SHOWN]) + (f" (+{len(ids) - SHOWN} more)" if len(ids) > SHOWN else "")
    return f"baseline-red: {shown}, bug {bug or 'not filed'}"
