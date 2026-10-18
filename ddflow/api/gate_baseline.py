"""`gate run`: a ci / unit_tests failure that the base branch shows too is recorded
`partial` as baseline-red (DX-gate-baseline-red). The check itself is
`services.gates.baseline_red`; this is the part that files the bug."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..infra import worktree as W
from ..services.gates import baseline_red as BR
from .knowledge.bugs import bug_found


def apply(repo, cfg, agent, it, gate, gdef, result, ev, rerun, fail_fast) -> tuple[str, str]:
    """``(outcome, reason)``: ``partial`` plus the baseline-red reason when every failing
    test fails on the base too, else ``result`` and "". Notes the check in ``ev``."""
    ids = BR.candidate_ids(gate, gdef.command, ev, fail_fast) if result == "failed" else []
    if not ids:
        return result, ""
    base = it.base or W.default_branch(repo)
    found = BR.on_base(repo, base, gdef, ids, rerun)
    ev["baseline"] = found
    if not found["red"]:
        return result, ""
    bug = _file(repo, agent, it.id, gate, ids, found)
    found["bug"] = bug
    return "partial", BR.reason_text(ids, bug)


def _file(
    repo: Path, agent: str, item: str, gate: str, ids: list[str], found: dict[str, Any]
) -> str:
    sha = found["base_sha"]
    out = bug_found(
        repo,
        id=BR.bug_id(ids, sha),
        item=item,
        agent=agent,
        severity="high",
        summary=(
            f"{found['base']} ({sha[:8]}) is red: {', '.join(ids)} fail on it as well as in "
            f"{item}'s {gate} gate (baseline-red)"
        ),
    )
    return str(out.data.get("id", "")) if out.exit == 0 else ""
