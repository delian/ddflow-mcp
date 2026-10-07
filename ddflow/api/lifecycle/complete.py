"""`complete`, `abandon`, `remove`, `block`, `unblock`.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.events import parse_changelog
from ...core.model import ABANDONED, DONE, REVIEW, State, fold
from ...services import leases as L
from .._base import _load
from ._common import _require
from .heartbeat import _commit_events, _waiters


def _session_model(st: State, agent: str) -> str:
    """The model `agent` declared on its most recent OPEN session, or "".

    Only this agent's: another agent's session names another author, and borrowing its
    model would judge reviewer independence against the wrong family.
    """
    open_ = [s for s in st.sessions.values() if s.agent == agent and s.model and not s.ended_at]
    return max(open_, key=lambda s: s.started_at).model if open_ else ""


def complete(
    repo: Path,
    item: str,
    *,
    sha: str = "",
    force: bool = False,
    model: str = "",
    agent: str = "",
    changelog: str = "",
    regression_test: str | list[str] = "",
) -> O.Outcome:
    """Finish an item, refusing on an incomplete pipeline unless forced.

    The rule-set lives in `services.completion`. This decides only what to DO with the
    verdict, and records the override when one is taken — an unrecorded `--force` is a
    pipeline that was never really enforced.

    ``regression_test`` closes the open bugs this item was filed to fix (its `fixes`,
    `CM.open_bugs_of`; never a bug merely reported against it, B7bdcc6b212) through `bug_fixed` -- the same refusals: a test must be named and
    must exist. The verdict is judged FIRST, with that one blocker lifted: a completion
    refused for anything else closes no bug (the bug closes when the task completes, not
    when the command is typed), and the flag on an item that fixes no open bug is refused
    rather than dropped on the floor.
    """
    from ...services import completion as CM

    entry: dict[str, Any] = {}
    if changelog:
        try:
            entry = parse_changelog(changelog)
        except ValueError as e:
            return O.failed("item.completed", str(e), id=item)
    log, cfg, st = _load(repo, agent)
    it = _require(st, item, "item.completed")
    if isinstance(it, O.Outcome):
        return it
    umbrella = _umbrella_children(st, it)
    closed: list[str] = []
    tests = [regression_test] if isinstance(regression_test, str) else list(regression_test)
    closing = [t for t in tests if t.strip()]
    pending = CM.open_bugs_of(st, item, cfg)
    if closing and not pending:
        return O.refused(
            "item.completed",
            f"--regression-test: {item} is not the fix task of any open bug, so there is "
            f"nothing for it to close -- drop the flag, or close a bug with `ddflow bug "
            f"fixed <bug> --regression-test ...`.",
            id=item,
            bugs_closed=[],
        )

    # The author is whoever completes; the model it declared at `session start` is its
    # model unless it says otherwise here (B7a5c63e3d2). Both surfaces arrive here, so
    # CLI and MCP default alike -- MCP's `clientInfo` names the harness, not a model.
    model = model or _session_model(st, log.agent_id)
    # No --sha: the commit `merge` recorded is the landing (the fold keeps it either
    # way), so report it rather than an empty string (B9f8019c521).
    sha = sha or it.merged_sha
    v = CM.verdict(st, cfg, item, repo=repo, model=model)
    if umbrella:
        # Its work is its sub-tasks', each of which ran its own pipeline: the umbrella
        # has no diff for implement, unit_tests or merge to judge (B651a63e574). An open
        # bug it was filed to fix still holds it, as for any item.
        v.blockers = [b for b in v.blockers if b.startswith(CM.OPEN_BUG_BLOCKER)]
    if closing:
        # The one blocker the flag is about to clear; every other one still stands.
        v.blockers = [b for b in v.blockers if not b.startswith(CM.OPEN_BUG_BLOCKER)]
    base: dict[str, Any] = {
        "id": item,
        "sha": sha,
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
    }
    if not v.may_complete and not force:
        return O.refused(
            "item.completed",
            f"cannot complete {item} — {len(v.blockers)} unmet condition(s):\n"
            + "\n".join(f"  - {b}" for b in v.blockers)
            + f"\n\n`ddflow gate status {item}` shows the pipeline. --force overrides, "
            f"and the override is recorded.",
            forced=False,
            **base,
        )
    forced = bool(v.blockers and force)
    if closing:
        # Only now, with the completion going through: a refusal above closed nothing.
        from ..knowledge import bug_fixed

        for bid in pending:
            out = bug_fixed(repo, bid, regression_test=tests, agent=agent)
            if out.exit != O.OK:
                return O.Outcome(
                    kind="item.completed",
                    data={"id": item, "bug": bid, "bugs_closed": closed},
                    exit=out.exit,
                    reason=f"--regression-test: bug {bid} not closed: {out.reason}",
                )
            closed.append(bid)
        base["bugs_closed"] = closed
    # BEFORE the completion event: a crash between the two leaves the reports with tasks
    # of their own and the item still completable, never stranded on a done task.
    from ..bug_reopen import refile_reported

    refiled = refile_reported(log, cfg, item, CM.reported_against(st, item, cfg))
    waiting = _waiters(repo, item)  # before the release: see `release`
    from ...services import ledger as LG

    log.append(
        "item.completed",
        item,
        {
            "ledger": LG.git_facts(repo, sha, it),
            "sha": sha,
            "kind": it.kind,
            "forced": forced,
            "overridden": v.blockers if force else [],
            # Optional, like `changelog`: the sub-tasks a settled umbrella completed with.
            **({"umbrella": umbrella} if umbrella else {}),
            # Optional (D-export (4)): an absent key is the old shape, so an older ddflow
            # folds this event exactly as before.
            **({"changelog": entry} if entry else {}),
        },
    )
    L.release(log, item, note="completed")
    extra: dict[str, Any] = {"bugs_refiled": refiled}
    from ...services import progress_line as PL

    # The item is complete and released by now: a report that cannot be built must
    # never make that look like a failed completion.
    try:
        progress = PL.report(fold(log.read_all(), strict=False), cfg, item)
    except Exception as e:  # informational only, see above
        progress = f"(progress report unavailable: {type(e).__name__}: {e})"
    if progress:
        extra["progress"] = progress
    if it.kind == "phase":  # [export].refresh = phase_close
        from ...services.export import refresh as RF

        rr = RF.refresh_selected(repo, "phase_close", cfg=cfg)
        if rr.outcomes:
            extra["export_refresh"] = {**rr.data(), "summary": rr.summary()}
    # A split task "completes when its children do" (B651a63e574): the last sub-task's
    # completion completes its umbrella, and that one's, up the chain.
    extra.update(_complete_umbrellas_above(repo, log, cfg, item, agent))
    extra.update(_commit_events(log, cfg, f"complete {item}"))
    return O.ok("item.completed", forced=forced, woke=waiting, **base, **extra)


def _umbrella_children(st: State, it) -> list[str]:
    """The done sub-tasks of a SETTLED umbrella, or [] when ``it`` is not one: a task split
    into sub-tasks, every one settled and at least one done.

    Its work is its sub-tasks', each of which ran its own pipeline (B651a63e574). All of
    them abandoned is not the work finished, and a phase keeps its own close."""
    if it.kind != "task" or it.state in (DONE, ABANDONED):
        return []
    below = sorted(st.descendants(it.id))
    if not below or st.open_descendants(it.id):
        return []
    return [i for i in below if st.items[i].state == DONE]


def _complete_umbrellas_above(repo: Path, log, cfg, item: str, agent: str) -> dict[str, Any]:
    """Complete the task umbrella just above ``item`` if this completion settled it --
    through `complete` itself, so it is recorded like any completion and climbs on from
    there. ``{"umbrellas_completed": [...]}``, plus ``umbrella_refused`` with the reason
    when that umbrella's own completion was refused (an open bug it was filed to fix):
    the caller's success must not hide an umbrella left open."""
    st = fold(log.read_all(), strict=False)
    parents = st.ancestors(item)
    if not parents or not _umbrella_children(st, parents[0]):
        return {"umbrellas_completed": []}
    up = parents[0].id
    done = complete(repo, up, agent=agent)
    if done.exit != O.OK:
        return {"umbrellas_completed": [], "umbrella_refused": {up: done.reason}}
    return {
        "umbrellas_completed": [up, *done.data.get("umbrellas_completed", [])],
        **(
            {"umbrella_refused": done.data["umbrella_refused"]}
            if done.data.get("umbrella_refused")
            else {}
        ),
    }


def _abandon_refused(item: str, reason: str, why: str) -> O.Outcome:
    """Built directly: `O.refused(kind, reason, **data)` owns `reason`, so passing the
    abandon reason as a wire field raised TypeError -- `abandon` on a DONE item crashed
    with exit 1 instead of refusing with exit 3, and the refusal text was never shown."""
    return O.Outcome(
        kind="item.abandoned", data={"id": item, "reason": reason}, exit=O.REFUSED, reason=why
    )


def abandon(
    repo: Path, item: str, *, reason: str = "", force: bool = False, agent: str = ""
) -> O.Outcome:
    """Stop work without completing, with a recorded reason.

    Distinct from `block`: a blocked item is waiting for something and will resume, an
    abandoned one will not. The phase completion check treats only `done` and `abandoned`
    as settled, so an item you decided against stops holding its phase open — which it
    otherwise does forever, since nothing else can ever finish it.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.abandoned")
    if isinstance(it, O.Outcome):
        return it
    if it.state == REVIEW and it.pr and it.pr.state == "open" and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} has an open request ({it.pr.url}). Abandoning it here leaves that "
            f"request open -- mergeable by anyone, recorded by no one -- and anything "
            f"stacked on it would be re-based onto the target carrying its commits. Close "
            f"the request (`pr sync` then parks it), or --force.",
        )
    if it.state == DONE and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} is already done; abandoning it would rewrite finished history. "
            f"--force if you really mean it.",
        )
    log.append("item.abandoned", item, {"reason": reason, "kind": it.kind})
    if it.lease:
        L.release(log, item, note=f"abandoned: {reason}")
    return O.Outcome(kind="item.abandoned", data={"id": item, "reason": reason})


def remove(
    repo: Path, item: str, *, reason: str = "", force: bool = False, agent: str = ""
) -> O.Outcome:
    """Take an item out of the queue entirely.

    The event log is append-only, so this RECORDS a removal rather than deleting anything:
    the item and everything that happened to it stay in the history and in `ddflow
    replay`, which is what keeps the record honest about work that was planned and then
    dropped.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.removed")
    if isinstance(it, O.Outcome):
        return it
    # Any item with work beneath it, not just a phase. The `kind == "phase"` guard
    # predates sub-tasks: removing a task umbrella left its children live but unreachable,
    # because `State.tasks(phase)` walks `descendants()` and `children()` skips a removed
    # node -- so the phase view reported "nothing actionable" while two open tasks sat
    # under the hole.
    kids = [t.id for t in st.open_descendants(item)]
    if kids and not force:
        return O.refused(
            "item.removed",
            f"{item} still has {len(kids)} task(s): {', '.join(kids[:8])}.\n"
            f"Remove them first, or --force to orphan them.",
            id=item,
            children=kids,
        )
    dependents = [o.id for o in st.items.values() if not o.removed and item in o.needs]
    if dependents and not force:
        return O.refused(
            "item.removed",
            f"{', '.join(dependents)} depend{'s' if len(dependents) == 1 else ''} on "
            f"{item}. Removing it would leave them blocked on something that no longer "
            f"exists (unknown dependencies are treated as unmet, deliberately).\n"
            f"Update them first, or --force.",
            id=item,
            dependents=dependents,
        )
    if it.lease:
        L.release(log, item, note="removed from the queue")
    log.append("phase.removed" if it.kind == "phase" else "task.removed", item, {"reason": reason})
    return O.ok("item.removed", id=item, children=[], dependents=[])


def block(
    repo: Path, item: str, *, reason: str = "", reopen: bool = False, agent: str = ""
) -> O.Outcome:
    """Park an item on something outside the queue — a vendor, an operator decision.

    The existence check is not ceremony. `fold`'s `_h_state` reaches items through
    `_item()`, which CREATES one when the id is unknown, so this was the only mutating
    command where a typo'd id materialised a titleless phantom task — which the scheduler
    then offered to an agent as the next thing to do.

    A DONE or ABANDONED item is refused (exit 3) unless `reopen`: blocking it moves finished work
    out of done, which a mistyped id would do silently (B-block-done). `reopen` is the
    deliberate form -- the B12/B14 data fix was one.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.blocked")
    if isinstance(it, O.Outcome):
        return it
    if it.state in (DONE, ABANDONED) and not reopen:
        if it.state == DONE:
            why = (
                f"{item} is DONE; blocking it would move finished work out of done (and "
                f"anything depending on it would wait again)."
            )
        else:
            why = (
                f"{item} is ABANDONED; blocking it would revive work that was dropped on "
                f"purpose (a mistyped id would do it silently)."
            )
        return O.refused(
            "item.blocked",
            f"{why} Pass --reopen (reopen=true over MCP) if that is the intent.",
            id=item,
        )
    log.append("item.blocked", item, {"reason": reason})
    return O.Outcome(kind="item.blocked", data={"id": item, "reason": reason})


def unblock(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    """Release a blocked item -- and every blocked item beneath it -- back into the queue.

    The subtree is what makes "drive this legacy section" one command. An import holds
    the open work of an archive file as blocked, one phase per section, and naming the
    section is how its work becomes work again (the source project's `phase.py next
    --session X`). Releasing thirty tasks one id at a time is how half of them stay held.

    Exit 2 when nothing under `item` is blocked: "nothing to release" is a fact the
    caller should see, not a success that wrote no event.
    """
    from ...core.model import BLOCKED

    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.unblocked")
    if isinstance(it, O.Outcome):
        return it
    targets = [item] if it.state == BLOCKED else []
    targets += sorted(
        d for d in st.descendants(item) if st.items[d].state == BLOCKED and not st.items[d].removed
    )
    if not targets:
        return O.nothing(
            "item.unblocked",
            f"{item} is {it.state} and nothing beneath it is blocked",
            id=item,
            was="",
            released=[],
        )
    was = it.blocked_reason if it.state == BLOCKED else ""
    for t in targets:
        log.append("item.unblocked", t, {"note": note, "was": st.items[t].blocked_reason})
    return O.ok("item.unblocked", id=item, was=was, released=targets)
