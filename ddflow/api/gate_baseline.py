"""`gate run`: a ci / unit_tests failure that the base branch shows too is recorded
`partial` as baseline-red (DX-gate-baseline-red). The check itself is
`services.gates.baseline_red`; this is the part that files the bug."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..infra import worktree as W
from ..services import testscope as TSC
from ..services.gates import baseline as BL
from ..services.gates import baseline_red as BR
from . import gate_passed_failures as PF
from ._base import _load
from .knowledge.bugs import bug_found


def apply(  # noqa: PLR0913, PLR0917
    repo, cfg, agent, it, gate, gdef, result, ev, rerun, fail_fast, cwd=None
) -> tuple[str, str]:
    """``(outcome, reason)``: ``partial`` plus the baseline-red reason when every failing
    test fails on the base too, else ``result`` and "". Notes the check in ``ev``.

    A command that exited 0 over output listing failing tests is settled first
    (`gate_passed_failures`): those tests are run alone, and without a passing rerun the gate
    is not ``passed``."""
    result, why = PF.apply(repo, cfg, it, gate, gdef, result, ev, rerun)
    if result != "failed":
        return result, why
    # After a rerun the tests in doubt are the ones that failed ALONE, not the whole listing.
    seen = (
        {
            **ev,
            "failed_tests": ev["rerun"]["still_failing"],
            "failed_tests_more": ev["rerun"].get("failed_tests_more", 0),
        }
        if why and ev["rerun"].get("still_failing")
        else ev
    )
    ids = BR.candidate_ids(gate, gdef.command, seen, fail_fast) if result == "failed" else []
    if not ids:
        return result, why
    base = it.base or W.default_branch(repo)
    found = BR.on_base(repo, base, gdef, ids, rerun, cfg.gates.baseline_cache_ttl_s)
    ev["baseline"] = found
    if not found["red"]:
        return result, why
    bug = _linked(repo, agent, ids) or _file(repo, agent, it.id, gate, ids, found)
    found["bug"] = bug
    found["auto"] = BL.auto_decision(cfg, cwd or repo, base, ids, found, TSC.limits(cfg))
    reason = BR.reason_text(ids, bug)
    if why:
        ev["reason"] = reason  # the headline names the base, not the superseded "NOT a pass"
    return "partial", reason


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
        tags="baseline-red",
        summary=(
            f"{found['base']} ({sha[:8]}) is red: {', '.join(ids)} fail on it as well as in "
            f"{item}'s {gate} gate (baseline-red)"
        ),
    )
    return str(out.data.get("id", "")) if out.exit == 0 else ""


def _linked(repo: Path, agent: str, ids: list[str]) -> str:
    """The open bug whose text already names every one of ``ids`` (dedup by test id), or ""."""
    _log, _cfg, st = _load(repo, agent)
    for b in st.bugs.values():
        if not (b.fixed_at or b.invalid_at) and all(_names(b.summary, i) for i in ids):
            return b.id
    return ""


def _names(text: str, test_id: str) -> bool:
    """Does ``text`` name ``test_id`` as a whole id (not as the start of a longer one)?"""
    return re.search(rf"(?<![\w:/.\[-]){re.escape(test_id)}(?![\w\]\[:-])", text) is not None
