"""`queue add`, `queue status`, `queue remove` and `merge --queue`: the agent's half of the
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
from ..services import mergequeue as MQ
from ._base import _load


def _unit_tests(cfg, unit_tests: bool | None) -> bool:
    return cfg.merge_queue.run_unit_tests if unit_tests is None else unit_tests


def refusal(repo: Path, cfg, st, it, *, unit_tests: bool) -> str:
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
    owed = MQ.owed_gates(repo, cfg, st, it, unit_tests=unit_tests)
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


def queue_add(
    repo: Path, item: str, *, unit_tests: bool | None = None, agent: str = ""
) -> O.Outcome:
    """Put ``item`` in line to be integrated. Exit 3 when it is not ready to land."""
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    why = refusal(repo, cfg, st, it, unit_tests=_unit_tests(cfg, unit_tests))
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
    entry, added = MQ.add(repo, item, agent=log.agent_id, keep_history=cfg.merge_queue.keep_history)
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
        ),
    )


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
