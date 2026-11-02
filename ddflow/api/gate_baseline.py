"""`gate run`: a ci / unit_tests failure that the base branch shows too is recorded
`partial` as baseline-red (DX-gate-baseline-red). The check itself is
`services.gates.baseline_red`; this is the part that files the bug."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..infra import worktree as W
from ..services import testscope as TSC
from ..services import testselect as TS
from ..services.gates import baseline as BL
from ..services.gates import baseline_red as BR
from ..services.gates.defs import load_gates
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
    probe = gdef
    if gate == "ci":  # `ddflow ci run` is no pytest: the whole failing list is kept aside, and
        probe = _unit_tests_def(repo, cfg) or gdef
        ids = [] if TS.fail_fast(probe.command) else _ci_ids(repo, cfg, ev, ids)
    if not ids:
        return result, why
    base = it.base or W.default_branch(repo)
    found = BR.on_base(repo, base, probe, ids, rerun, cfg.gates.baseline_cache_ttl_s)
    ev["baseline"] = found
    if not found["red"]:
        fresh = [i for i in ids if i not in set(found.get("still_failing") or ())]
        if fresh:
            found["new_failures"] = fresh[: BR.SHOWN * 4]
            why = (why + "; " if why else "") + (
                f"new failure(s) not failing on {base}: {', '.join(fresh[: BR.SHOWN])}"
                f"{' ...' if len(fresh) > BR.SHOWN else ''} (a timing flake counts as new unless "
                f"it also fails on the base)"
            )
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
    """Does ``text`` name ``test_id`` as a whole id (not inside a longer one or a path)?"""
    left = r"(?:(?<![^\s,;(`'\"\[]))"  # start, or after a space or opening punctuation
    right = r"(?=$|[\s,;)`'\"\]]|[.:](?:\s|$))"
    return re.search(left + re.escape(test_id) + right, text) is not None


def _ci_ids(repo, cfg, ev: dict[str, Any], capped: list[str]) -> list[str]:
    """Every failing id of the ci run: the whole list `ci run` kept aside for this commit, else
    the evidence's own when it was not cut ([] when the list is cut and no file holds it)."""
    sha = str((ev.get("ci_scope") or {}).get("sha") or "")
    return BL.read_full(repo, sha) or (capped if not ev.get("failed_tests_more") else [])


def _unit_tests_def(repo, cfg):
    """The gate that runs pytest: ci's failing ids are run on the base with ITS command."""
    return load_gates(repo, cfg).get("unit_tests")
