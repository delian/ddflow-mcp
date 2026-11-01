"""A contested claim: two clones each believe they hold an item, and ``resolve``
settles which does. The surfaces show one, and ``lost_contests`` reads which this
clone's agent lost.
"""

from __future__ import annotations

from typing import Any

from ...config import Config
from ...core import outcome as O
from ...core.model import Item, State
from ...core.schedule import plan_blocker
from ...infra.log import EventLog


def resolve_command(item_id: str) -> str:
    """The one command that settles a contest, spelled the same everywhere."""
    return f"ddflow resolve {item_id} --keep <event-id|agent>"


def contest_row(st: State, cfg: Config, it: Item) -> dict[str, Any]:
    """One contested item as the surfaces show it: the scheduler's own 'contested' sentence
    (`plan_blocker`, so `next`, `status`, `brief` and the refusals cannot word it apart),
    the rival claims, and the command that settles it."""
    blocker = plan_blocker(st, cfg, it)
    return {
        "id": it.id,
        "title": it.title,
        "summary": it.contest_summary(),
        "detail": blocker.detail if blocker is not None and blocker.reason == "contested" else "",
        "resolve": resolve_command(it.id),
        "claims": [{"holder": h["holder"], "event": h["event"]} for h in it.lease_candidates()],
    }


def contested_items(st: State) -> list[Item]:
    """Live, unfinished items whose definition or claim is contested, in id order."""
    return sorted(
        (i for i in st.live_items() if not i.terminal and i.contest_summary()),
        key=lambda i: i.id,
    )


def surface_contests(p, st: State, cfg: Config) -> None:
    """Move every contested item the plan lists as RUNNING to the head of its blocked list.

    A contested claim is displayed as a live lease, so the plan called the item 'running'
    and `status`, `next` and `brief` showed it as ordinary work in flight. It is blocked:
    two clones each believe they hold it, and `ddflow resolve` settles which does."""
    for it in reversed([i for i in p.running if i.contest_summary()]):
        blocker = plan_blocker(st, cfg, it)
        if blocker is not None:
            p.running.remove(it)
            p.blocked.insert(0, blocker)


def contest_refusal(it: Item, kind: str, doing: str) -> O.Outcome | None:
    """The refusal (exit 3) of ``doing`` ('merge', 'complete') on a contested item, or None.

    Run before any git action: a contested claim is optimistic, so both holders believe
    the item is theirs, and landing the loser's branch or finishing the item for the loser
    would settle the contest by whoever typed first."""
    summary = it.contest_summary()
    if not summary:
        return None
    return O.refused(
        kind,
        f"{it.id} is CONTESTED, so it cannot be {doing}: {summary}. "
        f"`{resolve_command(it.id)}` settles it; nothing was changed.",
        id=it.id,
        contested=True,
    )


def _branches_of(events, agent: str) -> dict[str, str]:
    """item -> the branch ``agent`` bound it to: its latest lease event naming one (the
    branch is bound after the claim, by a `lease.renewed`)."""
    out: dict[str, str] = {}
    for e in events:
        if e.data.get("holder") == agent and e.data.get("branch") and e.kind.startswith("lease."):
            out[e.subject] = e.data["branch"]
    return out


def _same_clone(theirs: str, clone: str) -> bool:
    """Is a claim recorded from clone ``theirs`` this clone's? A claim without a clone is."""
    return not (theirs and clone and theirs != clone)


def _ours(e, claimed: dict[str, Any], clone: str) -> bool:
    """Is the claim this release ends one THIS clone made?"""
    acq = claimed.get(e.data.get("event", ""))
    return _same_clone(acq.data.get("clone", "") if acq is not None else "", clone)


def lost_contests(log: EventLog, agent: str) -> dict[str, dict[str, Any]]:
    """The claims THIS clone's agent lost to a contest, newest per item: item -> {winner,
    branch, event}. Read off the `lease.released` that `resolve` marks ``lost_contest``,
    for the clone that made the claim (a claim records its clone: a release of another
    clone's claim under the same name is not ours). Dropped once THIS clone claimed the item
    again. The branch is the holder name's latest bound one, which equals this clone's own
    unless two clones of one name bind different branch names for an item (they derive it
    from the committed config and the item id, so they do not)."""
    events = log.read_all()
    clone = log.clone_id(create=False)
    claimed = {e.id: e for e in events if e.kind == "lease.acquired"}
    branch_of = _branches_of(events, agent)
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        mine = e.kind == "lease.released" and e.data.get("lost_contest")
        if not (mine and e.data.get("holder") == agent and _ours(e, claimed, clone)):
            continue
        if e.subject not in out or e.lamport >= out[e.subject]["lamport"]:
            out[e.subject] = {
                "winner": e.data.get("winner", ""),
                "branch": branch_of.get(e.subject, ""),
                "event": e.data.get("event", ""),
                "lamport": e.lamport,
            }
    for e in events:
        # THIS clone's re-claim only: another clone running under the same name claiming
        # the item is the very case the clone id tells apart.
        again = (
            e.kind == "lease.acquired"
            and e.data.get("holder") == agent
            and _same_clone(e.data.get("clone", ""), clone)
        )
        if again and e.subject in out and e.lamport > out[e.subject]["lamport"]:
            del out[e.subject]
    return out
