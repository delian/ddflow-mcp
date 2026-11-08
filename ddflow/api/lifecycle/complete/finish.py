"""`complete`: finish an item, refusing on an incomplete pipeline unless forced; then complete the umbrellas above it.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core import outcome as O
from ....core.events import parse_changelog
from ....core.model import fold
from ....services import completion as CM
from ....services import leases as L
from ....services import ledger as LG
from ....services import progress_line as PL
from ....services import umbrella as UM
from ....services.export import refresh as RF
from ....services.gates import skip_followup as SF
from ..._base import _load_fresh
from ...bug_reopen import refile_reported
from .._common import _chore_base, _chore_event, _require
from ..author import (
    _author_event_identity,
    _author_model,
    _recorded_identity,
    _remember_declared_model,
)
from ..heartbeat import _commit_events, _inbox, _waiters
from ..planning import plan_for
from .approval import _ask_hint, _auto_complete_unavailable, _override_by_approval, _person_force
from .checks import _failed_review, _na_map, _phase_suite, _refuted_extra, _umbrella_children
from .closing import _close_arg_refusal, _close_bugs, _phase_end_self_assessment
from .dispose import _dispose


def complete(  # noqa: PLR0913 -- one keyword per completion facet; the verdict is one story
    repo: Path,
    item: str,
    *,
    sha: str = "",
    force: bool = False,
    model: str = "",
    agent: str = "",
    changelog: str = "",
    regression_test: str | list[str] = "",
    mutation: dict[str, str] | None = None,
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
    approval: str = "",
    lint_exempt: str = "",
) -> O.Outcome:
    """Finish an item, refusing on an incomplete pipeline unless forced.

    ``approval`` (RA-consume-sites-3): an agent may not use `--force`, but it may spend an
    operator's approval of exactly these unmet conditions (`--approval request` files the
    request, the operator answers it in chat, `--approval ID` completes with the override
    recorded and the approval's stamp). A failed review gate is never lifted this way either.

    The rule-set lives in `services.completion`. This decides only what to DO with the
    verdict, and records the override when one is taken — an unrecorded `--force` is a
    pipeline that was never really enforced.

    ``lint_exempt`` (Be5610bf732) is the same close as `bug fixed --lint-exempt`: the reason a
    CI-filed lint bug has no test; it goes through `bug_fixed`, so the same refusals apply.

    ``regression_test`` closes the open bugs this item was filed to fix (its `fixes`,
    `CM.open_bugs_of`; never a bug merely reported against it, B7bdcc6b212) through `bug_fixed` -- the same refusals: a test must be named and
    must exist. The verdict is judged FIRST, with that one blocker lifted: a completion
    refused for anything else closes no bug (the bug closes when the task completes, not
    when the command is typed), and the flag on an item that fixes no open bug is refused
    rather than dropped on the floor.
    """

    entry: dict[str, Any] = {}
    if changelog:
        try:
            entry = parse_changelog(changelog)
        except ValueError as e:
            return O.failed("item.completed", str(e), id=item)
    # `_load_fresh`, never `_load`: a completion judges the item's CURRENT kind and
    # pipeline, and `_load` returns a scope-cached fold -- the MCP handshake's read burst
    # folds the log once and reuses it, so a `ddflow update --kind doc` appended since then
    # is invisible to it and the old pipeline (unit_tests) still blocks (Bf0a1039f9f). The
    # CLI is a fresh process every time and never hit this; the in-process MCP server did.
    log, cfg, st = _load_fresh(repo, agent)
    force = _person_force(force, agent)
    it = _require(st, item, "item.completed", cfg.lease.grace_s, log.clone_id(create=False))
    # B2809f4213b: a contested claim is settled by `resolve`, not by finishing
    refusal = (
        it if isinstance(it, O.Outcome) else L.contest_refusal(it, "item.completed", "completed")
    )
    if refusal is not None:
        return refusal
    umbrella = _umbrella_children(st, it)
    closed: list[str] = []
    tests = [regression_test] if isinstance(regression_test, str) else list(regression_test)
    closing = [t for t in tests if t.strip()] or [lint_exempt.strip()] * bool(lint_exempt.strip())
    pending = CM.open_bugs_of(st, item, cfg)
    bad = _close_arg_refusal(repo, item, closing, pending, tests, lint_exempt, regression_test)
    if bad is not None:
        return bad

    # The author is whoever completes; the model it declared at `session start` is its
    # model unless it says otherwise here (B7a5c63e3d2). Both surfaces arrive here, so
    # CLI and MCP default alike -- MCP's `clientInfo` names the harness, not a model.
    model, model_source = _author_model(st, log.agent_id, model, cfg)
    recorded, family_only = _recorded_identity(model, model_source, cfg)
    # ONE builder for both outlets -- the caller-facing `base` dict and the durable
    # event spread the SAME dict, so the two cannot drift apart (they once re-derived
    # the key and value each on their own; roborev flagged the drift class).
    author_identity = _author_event_identity(recorded, model_source, family_only)
    # No --sha: the commit `merge` recorded is the landing (the fold keeps it either
    # way), so report it rather than an empty string (B9f8019c521).
    sha = sha or it.merged_sha
    # An umbrella's gates get a rolled-up outcome naming its sub-tasks (Bd37a921536).
    judge = UM.verdict if umbrella else CM.verdict
    v = judge(
        st,
        cfg,
        item,
        repo=repo,
        model=model,
        **({"log": log, "kids": umbrella} if umbrella else {}),
    )
    v.blockers += [b for b in (_phase_suite(repo, cfg, log, st, it, agent),) if b]
    hard = _failed_review(repo, cfg, st, item, v, umbrella)
    hard += CM._own_branch_unmerged(st, cfg, it, repo)
    if closing:
        # The one blocker the flag is about to clear; every other one still stands.
        v.blockers = [b for b in v.blockers if not b.startswith(CM.OPEN_BUG_BLOCKER)]
    base: dict[str, Any] = {
        "id": item,
        "sha": sha,
        **author_identity,
        "independence": v.independence,
        # BOTH surfaces, always. This used to print only in human mode, so an agent over
        # MCP -- which is always JSON -- completed the item and was never told a gate had
        # not run: the one fact most worth surfacing, invisible on precisely the surface
        # that needed it.
        "coverage_gaps": v.coverage_gaps,
        "note": v.coverage_note,
        "warnings": v.warnings,
        "blockers": v.blockers,
        "bugs_closed": closed,
        **_chore_base(v),
    }
    force, stamp, denied = _override_by_approval(
        log, cfg, item, v, hard, force, approval, agent, base
    )
    if denied is not None:
        _remember_declared_model(st, log, cfg, model, model_source)
        return denied
    if hard and force:
        _remember_declared_model(st, log, cfg, model, model_source)
        return O.refused(
            "item.completed",
            f"cannot complete {item}, and --force does not lift this:\n"
            + "\n".join(f"  - {b}" for b in hard),
            forced=False,
            **base,
        )
    force, stamp = _auto_complete_unavailable(log, cfg, it, v, hard, approval, agent, force, stamp)
    if not v.may_complete and not force:
        _remember_declared_model(st, log, cfg, model, model_source)
        return O.refused(
            "item.completed",
            f"cannot complete {item} — {len(v.blockers)} unmet condition(s):\n"
            + "\n".join(f"  - {b}" for b in v.blockers)
            + f"\n\n`ddflow gate status {item}` shows the pipeline. "
            + (
                "--force does not lift the review-gate blocker above."
                if hard
                else "--force overrides, and the override is recorded." + _ask_hint(agent)
            ),
            forced=False,
            **base,
        )
    forced = bool(v.blockers and force)
    if closing:
        # Only now, with the completion going through: a refusal above closed nothing.

        failed = _close_bugs(repo, item, pending, closed, tests, mutation, lint_exempt, agent)
        if failed is not None:
            return failed
        base["bugs_closed"] = closed
    # BEFORE the completion event: a crash between the two leaves the reports with tasks
    # of their own and the item still completable, never stranded on a done task.

    refiled = refile_reported(log, cfg, item, CM.reported_against(st, item, cfg))
    waiting = _waiters(repo, item)  # before the release: see `release`

    log.append(
        "item.completed",
        item,
        {
            "ledger": LG.git_facts(repo, sha, it),
            "sha": sha,
            "kind": it.kind,
            "forced": forced,
            "overridden": v.blockers if force else [],
            **stamp,
            # Optional, like `changelog`: the sub-tasks a settled umbrella completed with.
            **({"umbrella": umbrella} if umbrella else {}),
            # Optional (B-complete-requires-model-even-when-recorded): the author model
            # this judged against, and where it came from. The flag path has no session
            # record to carry the id, so the event names both for any known source. A
            # family-only author ([agent].family, a bare family after --model) records
            # `author_family` instead: the family only, never a model id (DX-author-family).
            **author_identity,
            # Optional (D-export (4)): an absent key is the old shape, so an older ddflow
            # folds this event exactly as before.
            **({"changelog": entry} if entry else {}),
            **_chore_event(v, st, cfg, it, repo),
            # Optional, additive (no new event kind): the gates the ACTUAL diff excused,
            # {gate: why}, so a required gate that did not run is accounted for on the
            # durable record (B-na-required-gate). Absent = none excused.
            **({"not_applicable": _na_map(v.na_evidence)} if v.na_evidence else {}),
        },
    )
    L.release(log, item, note="completed")
    SF.settle(log, st, cfg, item)
    extra: dict[str, Any] = {"bugs_refiled": refiled}
    extra.update(_dispose(repo, cfg, log, item, (called_from, shell_cwd)))
    extra.update(_refuted_extra(st, item))

    # The item is complete and released by now: a report that cannot be built must
    # never make that look like a failed completion.
    try:
        after = fold(log.read_all(), strict=False)
        # What `next` would offer now, not the bare queue: the block names it as "Next".
        progress = PL.report(
            after,
            cfg,
            item,
            plan=plan_for(repo, log, cfg, after, purpose="view", agent=log.agent_id),
        )
    except Exception as e:  # informational only, see above
        progress = f"(progress report unavailable: {type(e).__name__}: {e})"
    if progress:
        extra["progress"] = progress
    if it.kind == "phase":  # [export].refresh = phase_close
        rr = RF.refresh_selected(repo, "phase_close", cfg=cfg)
        if rr.outcomes:
            extra["export_refresh"] = {**rr.data(), "summary": rr.summary()}
        _phase_end_self_assessment(repo, changelog, agent, extra)
    # A split task "completes when its children do" (B651a63e574): the last sub-task's
    # completion completes its umbrella, and that one's, up the chain.
    extra.update(_complete_umbrellas_above(repo, log, cfg, item, agent))
    extra.update(_commit_events(log, cfg, f"complete {item}", "complete"))
    extra.update(_inbox(repo, cfg, cfg.agent.id or log.agent_id, consume=False))
    return O.ok("item.completed", forced=forced, woke=waiting, **base, **extra)


def _complete_umbrellas_above(repo: Path, log, cfg, item: str, agent: str) -> dict[str, Any]:
    """Complete the task umbrella just above ``item`` if this completion settled it --
    through `complete` itself, so it is recorded like any completion and climbs on from
    there. ``{"umbrellas_completed": [...]}``, plus ``umbrella_refused`` with the reason
    when that umbrella's own completion was refused (an open bug it was filed to fix):
    the caller's success must not hide an umbrella left open."""
    st = fold(log.read_all(), strict=False)
    parents = st.ancestors(item)
    if not parents or not _umbrella_children(st, parents[0]):
        return {"umbrellas_completed": [], "umbrella_refused": {}}
    up = parents[0].id
    done = complete(repo, up, agent=agent)
    if done.exit != O.OK:
        return {"umbrellas_completed": [], "umbrella_refused": {up: done.reason}}
    return {
        "umbrellas_completed": [up, *done.data.get("umbrellas_completed", [])],
        "umbrella_refused": dict(done.data.get("umbrella_refused") or {}),
    }
