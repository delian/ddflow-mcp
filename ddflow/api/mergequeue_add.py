"""`queue add`, `queue status` and `queue remove`: the agent's half of the
merge queue. The integrator's half is `api.mergequeue_run`; the store and the order are
`services.mergequeue`.

Enqueuing records a place in line and nothing else. It holds no lease and runs no check, so
the agent that enqueued goes on to other work; what it does require is that the item is
ready to land -- every gate the integrator does not produce itself already carries an
outcome -- because a queued merge is a merge, and a merge never skips a gate."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..infra import worktree as W
from ..services import lanerelease as LR
from ..services import leases as L
from ..services import mergequeue as MQ
from ._base import _load
from .lifecycle.author import _author_model, _recorded_identity


def _unit_tests(cfg, unit_tests: bool | None) -> bool:
    return cfg.merge_queue.run_unit_tests if unit_tests is None else unit_tests


def refusal(repo: Path, cfg, st, it, *, unit_tests: bool, also: tuple[str, ...] = ()) -> str:
    """Why ``it`` cannot be integrated now, or "". Asked when it is enqueued and again when
    the integrator takes it: a gate can be re-recorded failed in between."""
    if it is None or it.removed:
        return "no such item"
    if it.terminal:
        return "it is already finished"
    if cfg.flow.integration == "pr":
        return "[flow].integration = 'pr': landing is a person's decision, so there is no queue"
    if not it.worktree or not it.branch:
        return "it was claimed without a worktree, so it has no branch of its own to integrate"
    owed = MQ.owed_gates(repo, cfg, st, it, unit_tests=unit_tests, also=also)
    if owed:
        return (
            "gates that must carry an outcome before the integrator may take it: "
            + ", ".join(owed)
            + ". Record each (or `ddflow gate skip <id> <gate> --reason ...`)."
        )
    return ""


def _row(repo: Path, cfg, item: str) -> dict:
    rows = MQ.board(
        repo, window=cfg.merge_queue.eta_history, default_s=cfg.merge_queue.eta_default_s
    )
    return next((r for r in reversed(rows) if r["item"] == item), {})


def _release_plan(repo, cfg, st, it, log, wt_path, *, unit_tests, release: bool, agent: str):
    """(release info or None, why not). A plain `queue add` releases only when
    `[lanes].release_mode = "auto"` and the item is eligible; `--release` is refused with the
    reason instead."""
    if not release and cfg.lanes.release_mode != "auto":
        return None, ""
    caller = log.agent_id
    why = LR.refusal(repo, cfg, st, it, wt_path, unit_tests=unit_tests, caller=caller)
    if not why:
        model, source = _author_model(st, caller, "", cfg)
        author, _fam = _recorded_identity(model, source, cfg)
        if not author:
            why = (
                "no author identity: declare your model (`session start --model`) or [agent].family"
            )
    if why:
        return None, why
    lane = cfg.agent.id or caller
    return {"author": author, "lane": lane, "tree": LR.tree_of(wt_path)}, ""


def _enqueue(repo, cfg, st, it, log, wt_path, *, asked, release, ut, agent):
    """(entry, added, release plan), or the refusal Outcome of an explicit release that cannot
    be granted (never queued unreleased: the agent asked to leave). A release that merely
    falls short in auto mode queues the item without handing it over, and only if it is
    ready to land without the machine gates a release leaves to the integrator."""
    item = it.id
    plan = None
    if asked:
        plan, nope = _release_plan(repo, cfg, st, it, log, wt_path, unit_tests=ut,
                                   release=release, agent=agent)  # fmt: skip
        if nope and release:
            return O.refused("queue.add", f"{item} cannot be released: {nope}", id=item,
                             queued=False, released=False)  # fmt: skip
    plain = lambda: refusal(repo, cfg, st, it, unit_tests=ut)  # noqa: E731
    if plan is None and asked and (why := plain()):
        return O.refused("queue.add", f"{item} cannot be queued: {why}", id=item, queued=False)
    keep = cfg.merge_queue.keep_history
    try:
        entry, added = MQ.add(repo, item, agent=log.agent_id, release=plan, keep_history=keep,
                              release_cap=cfg.lanes.release_max)  # fmt: skip
    except MQ.ReleaseRefused as exc:
        if release:
            return O.refused("queue.add", f"{item} cannot be released: {exc}", id=item,
                             queued=False, released=False)  # fmt: skip
        if why := plain():
            return O.refused("queue.add", f"{item} cannot be queued: {why}", id=item, queued=False)
        plan = None
        entry, added = MQ.add(repo, item, agent=log.agent_id, keep_history=keep)
    return entry, added, plan


def queue_add(
    repo: Path,
    item: str,
    *,
    unit_tests: bool | None = None,
    agent: str = "",
    release: bool = False,
) -> O.Outcome:
    """Put ``item`` in line to be integrated. Exit 3 when it is not ready to land. With
    ``release`` the agent also hands over the item: its lease ends, its inbox is told."""
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    ut = _unit_tests(cfg, unit_tests)
    asked = release or (cfg.lanes.release_mode == "auto" and it is not None)
    why = refusal(repo, cfg, st, it, unit_tests=ut, also=LR.machine_gates(cfg) if asked else ())
    if why and asked and not release:  # auto mode: not eligible is a plain enqueue
        why, asked = refusal(repo, cfg, st, it, unit_tests=ut), False
    if why:
        return O.refused("queue.add", f"{item} cannot be queued: {why}", id=item, queued=False)
    wt = W.Worktree(item=item, path=W.load_path(repo, it.worktree), branch=it.branch, base="")
    if dirty := W.dirty(wt):
        return O.refused(
            "queue.add",
            f"{len(dirty)} uncommitted file(s) in {wt.path}: commit them before {item} is "
            f"queued, the integrator lands only what is committed. First: {dirty[0]}",
            id=item,
            queued=False,
            dirty=list(dirty),
            path=str(wt.path),
        )
    got = _enqueue(
        repo, cfg, st, it, log, wt.path, asked=asked, release=release, ut=ut, agent=agent
    )
    if isinstance(got, O.Outcome):
        return got
    entry, added, plan = got
    freed = _hand_over(repo, cfg, log, it, entry, plan) if plan and entry.released else {}
    row = _row(repo, cfg, item)
    where = f"position {row.get('position', 0)}" if row.get("position") else entry.state
    return O.ok(
        "queue.add",
        id=item,
        queued=True,
        added=added,
        state=entry.state,
        position=row.get("position", 0),
        eta_s=row.get("eta_s", 0),
        text=(
            f"{item} {'queued' if added else 'was already queued'} ({where}); "
            f"`ddflow queue run` integrates it, `ddflow queue status` shows the line."
            + (f" {LR.ready_text(item)}" if freed.get("released") else "")
        ),
        **freed,
    )


def _hand_over(repo: Path, cfg, log, it, entry: MQ.Entry, plan: dict) -> dict:
    """The agent's side of a release, after the entry exists: the event, the lease ended, the
    lane told. The entry already holds custody, so a failure here loses nothing."""
    item = it.id
    pipe = list(cfg.lanes.release_gates)
    log.append(LR.EVENT, item, LR.event_data(plan["author"], plan["lane"], plan["tree"], pipe,
                                             list(LR.machine_gates(cfg))))  # fmt: skip
    gone = L.release(log, item, note="released for gate (merge queue)")
    told = LR.notify(repo, cfg, plan["lane"], LR.ready_text(item), item=item,
                     reason="ready", sender="merge-queue")  # fmt: skip
    return {"released": True, "lease_released": bool(gone), "inbox_note": told}


def queue_remove(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """Take ``item`` out of the line (or forget its parked entry)."""
    _load(repo, agent)
    entry = MQ.get(repo, item)
    if entry is None:
        return O.nothing("queue.remove", f"{item} has no entry in the merge queue", id=item)
    if entry.state == MQ.RUNNING:
        return O.refused(
            "queue.remove", f"{item} is being integrated right now; wait for it", id=item
        )
    gone = MQ.remove(repo, item)
    return O.ok("queue.remove", id=item, removed=gone is not None, was=entry.state)


def queue_status(repo: Path, *, agent: str = "") -> O.Outcome:
    """The line: order, state, reason and ETA of every entry, and who is integrating."""
    _log, cfg, _st = _load(repo, agent)
    rows = MQ.board(
        repo, window=cfg.merge_queue.eta_history, default_s=cfg.merge_queue.eta_default_s
    )
    who = MQ.holder(repo)
    waiting = [r for r in rows if r["state"] in (MQ.QUEUED, MQ.RUNNING)]
    return O.ok(
        "queue.status",
        entries=rows,
        waiting=len(waiting),
        parked=sum(1 for r in rows if r["state"] == MQ.PARKED),
        integrator=who,
        eta_s=waiting[-1]["eta_s"] if waiting else 0,
    )
