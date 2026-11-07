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
    probe, ids = _probe_and_ids(gate, gdef, repo, cfg, ev, ids)
    if not ids:
        return result, why
    base = it.base or W.default_branch(repo)
    found = BR.on_base(
        repo,
        base,
        probe,
        ids,
        rerun,
        cfg.gates.baseline_cache_ttl_s,
        cwd or repo,
        BR._failure_sigs(seen),
    )
    ev["baseline"] = found
    if not found["red"]:
        return _not_red(ev, found, base, ids, result, why)
    if unran := _guards_unran(gate, ev):
        ev["reason"] = unran
        return result, unran
    unnamed = _unnamed(repo, agent, ids)
    bug = _linked(repo, agent, ids)
    if not bug and unnamed:
        bug = _file(repo, agent, it.id, gate, unnamed, found)
    found["bug"] = bug
    found["auto"] = BL.auto_decision(cfg, cwd or repo, base, ids, found, TSC.limits(cfg))
    reason = BR.reason_text(ids, bug)
    if not bug:
        not_filed = found.get("not_filed")
        if not_filed:
            reason = f"{reason}: {not_filed}"
    if why:
        ev["reason"] = reason  # the headline names the base, not the superseded "NOT a pass"
    return "partial", reason


def _not_red(
    ev: dict[str, Any], found: dict[str, Any], base: str, ids: list[str], result: str, why: str
) -> tuple[str, str]:
    """The outcome when the base does not show the whole failure. An id it passes or lacks is a
    new failure and fails the gate. An id it could not measure is neither: with nothing else
    to go on the gate is `unavailable`, since that run says nothing about this change."""
    if found.get("compared") is False:
        return result, why
    blind = set(found.get("unmeasured") or ())
    still = set(found.get("still_failing") or ())
    changed = list(found.get("different") or ())
    plain = [i for i in ids if i not in blind and i not in still and i not in set(changed)]
    if plain or changed:
        found["new_failures"] = (plain + changed)[: BR.SHOWN * 4]
        return result, (why + "; " if why else "") + _new_reason(base, plain, changed, blind)
    if not blind:
        return result, why
    shown = ", ".join(sorted(blind)[: BR.SHOWN]) + (" ..." if len(blind) > BR.SHOWN else "")
    how = found.get("why") or "the run on it gave no verdict for them"
    reason = f"baseline not measured on {base} ({how}): failing here, unknown there: {shown}"
    if why:
        ev["reason"] = reason
    return "unavailable", reason


def _new_reason(base: str, plain: list[str], changed: list[str], blind: set[str]) -> str:
    """The reason text for ids the base does not fail as it does here: ``plain`` are not failing
    there, ``changed`` fail there too but differently (a new offender in an already-red test)."""
    parts: list[str] = []
    if plain:
        parts.append(
            f"new failure(s) not failing on {base}: {', '.join(plain[: BR.SHOWN])}"
            f"{' ...' if len(plain) > BR.SHOWN else ''}"
        )
    if changed:
        parts.append(
            f"new offender(s) in an already-red test (fails on {base} too, but "
            f"differently): {', '.join(changed[: BR.SHOWN])}"
            f"{' ...' if len(changed) > BR.SHOWN else ''}"
        )
    if blind:
        parts.append(f"{len(blind)} more could not be measured there")
    return "; ".join(parts) + " (a timing flake counts as new unless it also fails on the base)"


def _guards_unran(gate: str, ev: dict[str, Any]) -> str:
    """Why a ci run's baseline-red conclusion must not stand ("" when it may): the selected
    run had always-run guards planned (`[ci].always_tests`, `ALWAYS = True`) that never ran to a
    verdict -- the selection failed first, so they were skipped. Every selected failure being
    baseline-red would otherwise read as 'nothing new' while the guards, whose input is the whole
    code base, never got to catch a regression (B47a1dbeba0)."""
    if gate != "ci":
        return ""
    scope = ev.get("ci_scope") or {}
    if scope.get("scope") != "selected" or not scope.get("always"):
        return ""  # full scope ran everything; no guards were planned to run after the selection
    verdict = (scope.get("ran") or {}).get("always")
    if verdict is not None and verdict != "unavailable":
        return ""  # they ran to a verdict; a guard failure is among the failing ids
    guards = scope.get("always_tests") or ()
    names = ", ".join(str(g) for g in guards[: BR.SHOWN]) + (
        " ..." if len(guards) > BR.SHOWN else ""
    )
    return (
        "the selected tests failed only as the base fails them (baseline-red), but the "
        f"always-run guards never ran ({scope.get('always')} guard file(s)"
        + (f": {names}" if names else "")
        + " -- they run after the selection), so a regression in them cannot be ruled out: "
        "the run is not baseline-red"
    )


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
    if out.exit == 0:
        return str(out.data.get("id", ""))
    # A refusal (a near-duplicate, an invalid field) is not "filed": say WHY, never a silent
    # "" that reads as "bug not filed" with nothing to act on (B-baseline-bug-not-filed-silently).
    found["not_filed"] = out.reason
    return ""


def _linked(repo: Path, agent: str, ids: list[str]) -> str:
    """The open bug to link the whole failing set to, or "" when some id no open bug names.

    Dedup is per id, not per set (B-baseline-red-filer-piles-up): an id already named by
    ANY open bug is that bug's, so a run whose ids are each owned by (possibly different)
    open bugs files nothing new. When every id is owned, the bug naming the whole set is
    returned if there is one, else the open bug owning the first id."""
    _log, _cfg, st = _load(repo, agent)
    open_bugs = [b for b in st.bugs.values() if not (b.fixed_at or b.invalid_at)]
    if any(not any(_names(b.summary, i) for b in open_bugs) for i in ids):
        return ""
    for b in open_bugs:
        if all(_names(b.summary, i) for i in ids):
            return b.id
    return next(b.id for b in open_bugs if _names(b.summary, ids[0]))


def _unnamed(repo: Path, agent: str, ids: list[str]) -> list[str]:
    """The failing ids no open bug names: the genuinely new failures ``_file`` files."""
    _log, _cfg, st = _load(repo, agent)
    open_bugs = [b for b in st.bugs.values() if not (b.fixed_at or b.invalid_at)]
    return [i for i in ids if not any(_names(b.summary, i) for b in open_bugs)]


def _names(text: str, test_id: str) -> bool:
    """Does ``text`` name ``test_id`` as a whole id (not inside a longer one or a path)?"""
    left = r"(?:(?<![^\s,;(`'\"\[=]))"  # start, or after a space or opening punctuation
    right = r"(?=$|[\s,;)`'\"\]]|[.:](?:\s|$))"
    return re.search(left + re.escape(test_id) + right, text) is not None


def _probe_and_ids(
    gate, gdef, repo, cfg, ev: dict[str, Any], ids: list[str]
) -> tuple[Any, list[str]]:
    """``(probe, ids)``: the gate whose command runs the failing ids on the base, and the whole
    failing list to try there. `ddflow ci run` is no pytest (its whole list is kept aside, run
    with the unit_tests command); a full-suite unit_tests failure caps its list the same way and
    keeps the whole one aside too (B69d1c0ff00)."""
    probe = gdef
    if gate == "ci":
        probe = _unit_tests_def(repo, cfg) or gdef
        ids = _ci_ids(repo, cfg, ev, ids)
        if TS.fail_fast(probe.command):
            ids = []  # a run stopped at its first failure lists one of possibly many
    elif gate == "unit_tests":
        ids = _unit_ids(repo, ev, ids)
    return probe, ids


def _ci_ids(repo, cfg, ev: dict[str, Any], capped: list[str]) -> list[str]:
    """Every failing id of the ci run: the whole list `ci run` kept aside for this commit, else
    the evidence's own when it was not cut ([] when the list is cut and no file holds it)."""
    sha = str((ev.get("ci_scope") or {}).get("sha") or "")
    return BL.read_full(repo, sha) or (capped if not ev.get("failed_tests_more") else [])


def _unit_ids(repo, ev: dict[str, Any], capped: list[str]) -> list[str]:
    """Every failing id of a unit_tests run whose list was cut: the whole list the run kept
    aside for its own tree (``runner.run_command_gate``), else the evidence's own when it was
    not cut ([] when the list is cut and no file holds it)."""
    sha = str(ev.get("tree_sha") or "")
    return BL.read_full(repo, sha) or (capped if not ev.get("failed_tests_more") else [])


def _unit_tests_def(repo, cfg):
    """The gate that runs pytest: ci's failing ids are run on the base with ITS command."""
    return load_gates(repo, cfg).get("unit_tests")
