"""The lead channel: the lead's view of the lanes, and the ask/answer relay.

A lead keeps the main task and the conversation with the operator; lanes are workers.
This module is the lead's side of that channel, built on the lane ledger (``ledger``),
the schedule's critical path, and the per-lane inbox (``services.inbox``, LC-wake). The
view is read-only; ``message`` and ``answer`` write one message to ONE inbox, so nothing
here starts or stops an agent, and a message is data, never an instruction.

The ETA is a FLOOR IN OPEN ITEMS read off the critical path -- the one number the log
supports without inventing a wall-clock. There is no per-gate duration telemetry to
project minutes from, so minutes are never invented here.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...core.records import Item, State
from ...core.schedule import critical_path
from .. import inbox as IB
from ..inbox import Message
from .ledger import Lane
from .render import render_ledger
from .safe import safe


def eta(item: Item | None, state: State) -> str:
    """An honest time-to-done for a lane's held item, or "" when there is none.

    The estimate is how many open items still sit after the item on the longest
    dependency chain -- its remaining critical-path floor. An item off the critical path
    has no such floor (its ETA is slack, so we say nothing rather than invent one).
    """
    if item is None:
        return ""
    path = critical_path(state)
    if item.id not in path:
        return ""
    after = len(path) - path.index(item.id) - 1
    if after:
        return f"ETA: {after} open item(s) after it on the critical path"
    return "ETA: last open item on the critical path"


def summary(ledger: Sequence[Lane], state: State, now: float) -> str:
    """The lead's rollup: the ledger table, then every lane's honest ETA where one exists."""
    lines = [render_ledger(ledger, now)]
    for lane in ledger:
        estimate = eta(state.items.get(lane.item), state)
        if estimate:
            lines.append(f"  {safe(lane.name)}: {estimate}")
    return "\n".join(lines)


def _caps(cfg=None):
    """The inbox caps used by the relay, read from ``[lease]`` when a config is at hand."""
    if cfg is None:
        return 2000, 50, 0.0
    return cfg.lease.inbox_max_chars, cfg.lease.inbox_max_pending, cfg.lease.inbox_ttl_s


def pending(repo: Path, lead: str) -> list[Message]:
    """The lead's pending inbox: lane questions await them here, unfenced data."""
    return IB.pending(repo, lead) if lead else []


def message(
    repo: Path,
    to: str,
    text: str,
    *,
    sender: str = "",
    item: str = "",
    reason: str = "message",
    cfg=None,
) -> tuple[Message | None, str]:
    """A steer or note into one lane's inbox (reason ``message``), as a lead or a lane."""
    max_chars, max_pending, ttl_s = _caps(cfg)
    return IB.send(
        repo,
        to,
        text,
        reason=reason,
        sender=sender,
        item=item,
        max_chars=max_chars,
        max_pending=max_pending,
        ttl_s=ttl_s,
    )


def answer(
    repo: Path,
    to: str,
    text: str,
    *,
    sender: str = "",
    item: str = "",
    cfg=None,
) -> tuple[Message | None, str]:
    """Relay an answer to one lane that actually asked, never a sweep.

    The ask is a pending message FROM ``to`` (the asking lane) in ``sender``'s (the lead's)
    inbox about ``item``. An answer with no matching pending ask is refused, so a lead
    cannot forward an operator's reply to every lane that did not ask (the parked patch's
    sweep). The answer is a ``message`` -- the lane pairs it to its question by ``item``
    and withdraws the ask.
    """
    if not sender:
        return None, "an answer names who relays it (the lead)"
    asks = IB.pending(repo, sender)
    if not any(m.sender == to and (not item or m.item == item) for m in asks):
        return None, f"no pending ask from {to or 'that lane'} to answer"
    return message(repo, to, text, sender=sender, item=item, reason="message", cfg=cfg)
