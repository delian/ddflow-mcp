"""A gate that fails the way the base branch fails is baseline-red, not the branch's fault
(DX-gate-baseline-red, B23bd81462d).

When `ci` or `unit_tests` fails with test ids read from its output, only those ids are run
again in a scratch worktree of the base. If every one of them fails there too, the failure
is not this change's: the gate is recorded `partial` (which `complete` accepts only once a person
has accepted it, `services.gate_accept`) with the reason ``baseline-red: <ids>, bug <id>``. Any id that passes on
the base, any id the output did not name (a capped list, a fail-fast run) or a command that
does not run pytest leaves the gate FAILED.

A run that executed nothing measured nothing (B-baseline-branch-only-test-no-tests-ran): an id
it leaves is `unmeasured`, neither passing nor failing there. A test the base LACKS is known
without running it (it cannot fail there, so it is new) and is kept out of the run, because one
unknown path or node id makes pytest abort every id beside it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...infra import worktree as W
from .. import testselect as TS
from .baseline import cache_load, cache_signatures, cache_store
from .testcmd import counts_of

GATES = ("ci", "unit_tests")
SHOWN = 5
#: Ids run on the base at once: under `flakes.MAX_FAILED_TESTS`, which cuts a run's listed failures.
CHUNK = 40
#: Said in the evidence: the comparison is only as clean as a fresh checkout is.
ISOLATION = (
    "fresh detached worktree of the base; the project's own command ran in it, so an "
    "editable install of the branch or git-ignored fixtures can still mislead it"
)
#: pytest's exits that ran no test: a usage error (a path or node id it cannot find, a broken
#: conftest), nothing collected.
ABORTED = (4, 5)
#: Its exits that stopped somewhere: interrupted (a collection error too), internal error. Only
#: the ids it reached have an answer, and `-q` does not say which those are.
CUT_SHORT = (2, 3)
#: What a summary counts as a test that ran; an `error` may be a file that never collected.
EXECUTED = ("passed", "failed", "skipped", "xfailed", "xpassed")
#: Times one chunk is retried without the ids pytest said the base lacks.
ROUNDS = 3
#: There is no probe for such a command, so nothing was lost: the failure stands as it was.
NOT_PYTEST = "the gate's command does not run pytest"
_NOT_FOUND = re.compile(r"^ERROR: (?:file or directory )?not found: (.+)$", re.M)
_PARAM = re.compile(r"^(.*?::[^\[]*)\[.*\]$")


def candidate_ids(gate: str, command: str, ev: dict[str, Any], fail_fast: bool) -> list[str]:
    """The failing ids to try on the base ([] when the failure cannot be attributed)."""
    if gate not in GATES or fail_fast or ev.get("failed_tests_more"):
        return []
    return list(ev.get("failed_tests") or [])


def _pinned_base(sha: str) -> str:
    """The base to measure against: the exact ``base`` commit the candidate merged, which the
    caller already resolved as ``sha`` (the same ``base`` the ci merge just used), not the
    candidate's own merge-base with ``base`` -- the fork point is older, so a test ``base``
    broke since the fork reads as a new failure there (B-ci-baseline-older-than-candidate;
    B48c70b6773's earlier merge-base pin had this wrong). The ci runs the branch MERGED with
    ``base``'s tip, so a test red on that tip but green at the fork is the base's own failure.
    When git cannot resolve ``base`` the caller's ``sha`` stands."""
    return sha


def on_base(
    repo: Path,
    base: str,
    gdef,
    ids: list[str],
    run: Callable[[Any, Path], tuple],
    ttl_s: int = 0,
    tree: Path | None = None,
    before: dict[str, str] | None = None,
) -> dict:
    """Run ``ids`` alone on ``base``: ``{"red": bool, ...}``. ``run(gdef, cwd)`` executes a
    command gate and returns (outcome, evidence). With ``ttl_s`` > 0 what was measured on this
    base sha is kept (`services.gates.baseline`): ids already measured are not run again.
    ``tree`` is the checkout that failed them (default ``repo``): what it has and the base has
    not is the branch's own.     ``before`` is the branch's own failure text per id
    (`_failure_sigs(ev)`): a test the base also fails, but differently, is a new offender, not
    baseline-red (B-baseline-red-hides-new-offender-in-red-ratchet)."""
    if not ids:  # all() of nothing is True: no named test is no attribution
        return {"red": False, "why": "no failing test id to run on the base"}
    sha = W.rev(repo, base)
    if not sha:
        why = f"base {base} does not resolve"
        return {"red": False, "why": why, "unmeasured": ids, "still_failing": []}
    sha = _pinned_base(sha)
    known = cache_load(repo, sha, ttl_s, None, gdef.command)
    cached_sigs = cache_signatures(repo, sha, ttl_s, None, gdef.command)
    where = {"base": base, "base_sha": sha}
    # A cache entry measured before failure text was kept (old format, no text) has nothing to
    # compare against: re-run it so a later new offender is spotted, its id-only verdict stands
    # meanwhile (B-baseline-red-hides-new-offender-in-red-ratchet).
    todo = [i for i in ids if i not in known or (known.get(i) and i not in cached_sigs)]
    if not todo:
        return {**_tally(ids, known, before, cached_sigs), **where, "cached": True, "ids": ids}
    ran = _run_chunks(repo, sha, gdef, todo, run, ttl_s, tree or repo)
    if isinstance(ran, str):  # why nothing could be measured
        return _not_run(ran, _tally(ids, known, before, cached_sigs), where)
    outcomes, cmds, seen, base_sigs = ran
    found = _tally(ids, {**known, **seen}, before, {**cached_sigs, **base_sigs})
    last = outcomes[-1] if outcomes else ""
    return {**found, **where, "isolation": ISOLATION, "command": "; ".join(cmds), "ids": ids,
            "outcome": "failed" if found["red"] or "failed" in outcomes else last}  # fmt: skip


def _tally(
    ids: list[str],
    known: dict[str, bool],
    before: dict[str, str] | None = None,
    onbase: dict[str, str] | None = None,
) -> dict[str, Any]:
    """What ``known`` says of ``ids``: red only when every one of them failed on the base with
    the failure text the branch showed. A test the base also fails, but DIFFERENTLY, is a new
    offender, not baseline (B-baseline-red-hides-new-offender-in-red-ratchet): it goes to
    ``different``, so ``still`` no longer covers it and ``red`` misses it."""
    different = [i for i in ids if known.get(i) and _differs(i, before, onbase)]
    still = [i for i in ids if known.get(i) and i not in different]
    return {"red": len(still) == len(ids), "still_failing": still, "different": different,
            "unmeasured": [i for i in ids if i not in known]}  # fmt: skip


def _differs(test_id: str, before: dict[str, str] | None, onbase: dict[str, str] | None) -> bool:
    """Does the failure text the BRANCH showed for ``test_id`` differ from what the base showed?
    Only when both are known and disagree: a base whose text is unknown (a cached id measured
    before text was kept) never claims a new offender, and a branch whose text is unknown is
    left to the id-only answer."""
    base = (onbase or {}).get(test_id)
    if not base:
        return False
    branch = (before or {}).get(test_id)
    return bool(branch) and branch != base


def _failure_sigs(ev: dict[str, Any]) -> dict[str, str]:
    """The branch's own failure text per id ({} when the evidence kept none): what
    ``on_base`` compares against the base's text to spot a new offender in an already-red test."""
    return dict(ev.get("failed_reasons") or {})


def _not_run(why: str, found: dict[str, Any], where: dict[str, str]) -> dict[str, Any]:
    """``found`` of a base that could not be run for the reason ``why``."""
    if why == NOT_PYTEST:
        return {**found, **where, "why": why, "red": False, "compared": False, "unmeasured": []}
    return {**found, **where, "why": why, "red": False}


def _run_chunks(repo: Path, sha: str, gdef, todo: list[str], run, ttl_s: int, branch: Path):
    """``(outcomes, commands, {id: failed on the base}, {id: failure text on the base})`` of
    running ``todo`` on a scratch checkout of ``sha`` in chunks under the evidence cap on
    listed failures (a run's list is never cut); or the reason it could not. An id the dict
    lacks was not measured."""
    outcomes: list[str] = []
    cmds: list[str] = []
    known: dict[str, bool] = {}
    base_sigs: dict[str, str] = {}
    with W.scratch_tree(repo, sha, detach=True, prefix="ddflow-base.", quiet=True) as tree:
        if tree.path is None:
            return "no scratch worktree of the base could be made"
        known = {i: False for i in todo if _branch_only(tree.path, branch, i)}
        cache_store(repo, sha, known, ttl_s, gdef.command)
        rest = [i for i in todo if i not in known]
        for start in range(0, len(rest), CHUNK):
            ran = _probe(gdef, tree.path, rest[start : start + CHUNK], run)
            if isinstance(ran, str):
                return ran
            outcome, tried, seen, sigs = ran
            outcomes.append(outcome)
            cmds += tried
            known.update(seen)
            base_sigs.update(sigs)
            cache_store(repo, sha, seen, ttl_s, gdef.command, sigs)
    return outcomes, cmds, known, base_sigs


def _branch_only(root: Path, branch: Path, test_id: str) -> bool:
    """Is ``test_id`` in a file ``branch`` has and the base at ``root`` has not? Asked of both: an
    id relative to a directory the command enters (`cd app && pytest`) is at neither root."""
    path = test_id.split("::", 1)[0]
    return path.endswith(".py") and (branch / path).exists() and not (root / path).exists()


def _probe(gdef, root: Path, part: list[str], run):
    """``(outcome, commands, {id: failed on the base}, {id: failure text on the base})`` of one
    chunk, or why it could not be run. A run that executed nothing is asked which ids the base
    lacks and repeated without them; an id left out of the dict was not measured."""
    outcome = ""
    cmds: list[str] = []
    seen: dict[str, bool] = {}
    sigs: dict[str, str] = {}
    for _round in range(ROUNDS):
        cmd = TS.run_command(gdef.command, part, root)
        if not cmd:
            return NOT_PYTEST
        cmds.append(cmd)
        probe = replace(gdef, command=cmd)
        outcome, ev = run(probe, root)
        if not _no_verdict(outcome, ev, len(part)):
            return (
                outcome,
                cmds,
                {**seen, **_verdicts(probe, part, outcome, ev)},
                {
                    **sigs,
                    **_base_signatures(ev, part),
                },
            )
        lacking, unsure = _lacking(probe, root, part, ev, run)
        seen.update(dict.fromkeys(lacking, False))
        for test_id in unsure:  # alone, what pytest names can only be this id
            alone = _probe(gdef, root, [test_id], run)
            if not isinstance(alone, str):
                cmds += alone[1]
                seen.update(alone[2])
                sigs.update(alone[3])
        part = [i for i in part if i not in lacking and i not in unsure]
        if not part or not (lacking or unsure):
            break
    return outcome, cmds, seen, sigs


def _base_signatures(ev: dict[str, Any], part: list[str]) -> dict[str, str]:
    """The base run's own failure text per id ({} for ids whose text it kept none of)."""
    reasons = ev.get("failed_reasons") or {}
    return {i: reasons[i] for i in part if reasons.get(i)}


def _no_verdict(outcome: str, ev: dict[str, Any], asked: int) -> bool:
    """Did a run that RAN leave any of the ``asked`` ids unreached? Its exit says so, or it counts
    fewer tests than it was asked for (a kept `-k` deselects some). A failed run with no count
    at all is left to its listed failures."""
    code = ev.get("exit")
    if code in ABORTED:
        return True
    counts = counts_of(" ".join(ev.get("summary") or ()))
    ran = sum(counts.get(word, 0) for word in EXECUTED)
    if code in CUT_SHORT or outcome == "passed":
        return ran < asked
    return 0 < ran + counts.get("error", 0) < asked


def _lacking(probe, root: Path, part: list[str], ev: dict[str, Any], run) -> tuple[list, list]:
    """``(ids the base lacks, ids pytest's answer cannot tell apart)``. Only pytest naming an id
    says it is missing: the same exit is a broken conftest. pytest-xdist exits 5 and names none,
    so a serial run asks; an unknown name stops pytest at collection, so that runs no test."""
    named = _names(ev)
    if not named and ev.get("exit") in ABORTED:
        named = _names(run(replace(probe, command=f"{probe.command} -n 0"), root)[1])
    stems = [_PARAM.sub(r"\1", i) for i in part]
    lacking: list[str] = []
    unsure: list[str] = []
    for test_id, stem in zip(part, stems, strict=True):
        if any(_same(n, test_id) for n in named):
            lacking.append(test_id)
        elif any(_same(n, stem) for n in named):  # it prints `test_p` for `test_p[9]`
            (lacking if stems.count(stem) == 1 else unsure).append(test_id)
    return lacking, unsure


def _names(ev: dict[str, Any]) -> list[str]:
    """What the run's output says it could not find."""
    return _NOT_FOUND.findall(str(ev.get("tail") or ""))


def _same(printed: str, test_id: str) -> bool:
    """Is ``printed`` (pytest prints it absolute) ``test_id``?"""
    return printed == test_id or printed.endswith(f"/{test_id}")


def _verdicts(gdef, part: list[str], outcome: str, ev: dict[str, Any]) -> dict[str, bool]:
    """``{id: failed on the base}`` of one run; {} when it cannot say which ids PASS: it could
    not run, listed nothing, listed a cut list, or stopped at the first failure (`-x`)."""
    failing = set(ev.get("failed_tests") or ())
    whole = (
        outcome == "passed"
        or ev.get("exit") in CUT_SHORT  # `_no_verdict` found that it reached them all
        or (outcome == "failed" and failing and (len(part) == 1 or not TS.fail_fast(gdef.command)))
    )
    if not whole or ev.get("failed_tests_more"):
        return {}
    return {i: i in failing for i in part}


def bug_id(ids: list[str], sha: str) -> str:
    return "B" + D.of_obj([sorted(ids), sha[:12]], length=10)


def reason_text(ids: list[str], bug: str) -> str:
    shown = ", ".join(ids[:SHOWN]) + (f" (+{len(ids) - SHOWN} more)" if len(ids) > SHOWN else "")
    return f"baseline-red: {shown}, bug {bug or 'not filed'}"
