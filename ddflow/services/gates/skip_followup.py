"""The follow-up task a skipped gate files, and how it is settled.

Gated behind `[gates].skip_followup` (`auto` default | `warn` | `off`). One skip of one
gate files ONE future task whose scope is only to re-run that gate on that item later --
the log entry, the reminder and the effort to re-run the gate, never a repair of the
gate-check code and never a redo of the item's payload. The follow-up is SETTLED --
removed from the queue -- when its item is abandoned (`settle` from `abandon`),
completed (`settle` from `complete`), or the gate is re-recorded non-skipped (`settle`
from `gates.record`).
"""

from __future__ import annotations

from typing import Any

from ...config import Config
from ...core import ids as IDS
from ...core.model import Item, State, fold
from ...infra.log import EventLog
from ...services.items import TaskDraft, add_task


def _open(st: State, item: str, gate: str = "") -> list[Item]:
    """The follow-up tasks still in the queue that re-run a gate on ``item``.

    ``gate`` narrower than ``item`` names one gate's follow-up; empty means every gate."""
    return [
        i
        for i in st.items.values()
        if i.followup_of == item and not i.removed and (not gate or i.followup_gate == gate)
    ]


def _parent_phase(st: State, it: Item) -> str:
    """The nearest OPEN phase above ``it``, else "": a follow-up sits next to its item in
    the item's own phase, holding nothing open (it is settled when the item settles)."""
    for node in st.ancestors(it.id):
        if node.kind == "phase" and not node.removed:
            return node.id
    return ""


def _followup_id(cfg: Config, item: str, used: Any) -> str:
    """The follow-up's id: `[ids].gate_skip_followup` (`gskip-<item>-<seq>` by default)."""
    return IDS.make(cfg, "gate_skip_followup", used=used, parent=item).id


def _file(log: EventLog, st: State, cfg: Config, it: Item, gate: str) -> tuple[str, str]:
    """File the follow-up for ``it``'s skipped ``gate`` and return ``(filed, note)``.

    The state was folded under the log lock, so "fire once" is decide-then-append."""
    tid = _followup_id(cfg, it.id, IDS.taken(st))
    title = f"Re-run {gate} on {it.id}"
    body = (
        f"Re-run the skipped gate {gate} on {it.id} and record the honest outcome: "
        f"`ddflow gate run {it.id} {gate}` (or skip it again with a reason). The follow-up "
        f"is ONLY the re-run -- do not repair the gate-check code, do not redo {it.id}'s "
        f"payload. Settled automatically when {it.id} completes, is abandoned, or {gate} "
        f"is re-recorded non-skipped."
    )
    draft = TaskDraft(
        tid,
        parent=_parent_phase(st, it),
        title=title,
        needs=[],
        globs=[],
        body=body,
        tags=["skip-followup"],
        priority=cfg.schedule.bug_priority,
        extra={"followup_of": it.id, "followup_gate": gate},
    )
    added = add_task(
        log,
        st,
        cfg,
        draft,
        dedupe="a generated gate-skip follow-up: its (item, gate) is its own key",
    )
    if not added.ok:
        # A minted id and a valid phase: only a race could refuse it. The skip still
        # stands; say what happened rather than leaving silence about the follow-up.
        return "", f"no follow-up filed for {gate} on {it.id}: {added.problem}"
    return tid, f"filed follow-up task {tid} to re-run {gate} on {it.id}"


def file_for_skip(log: EventLog, cfg: Config, item: str, gate: str) -> tuple[str, str]:
    """File ONE follow-up for ``item``'s skipped ``gate``; ``(filed, note)``.

    `off` does nothing; `warn` names the follow-up and files none; `auto` files it unless
    one already re-runs that gate on that item (fire once). ``note`` is the sentence for
    the skip's output; empty only under `off`."""
    policy = cfg.gates.skip_followup
    if policy == "off":
        return "", ""
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        it = st.items.get(item)
        if it is None or it.removed or it.terminal:
            return "", ""
        held = _open(st, item, gate)
        if held:
            return "", f"{held[0].id} already re-runs {gate} on {item}"
        if policy == "warn":
            return "", (
                f"NOTE: {item}.{gate} is skipped; [gates].skip_followup = 'warn' "
                f"reports it and files no follow-up to re-run it"
            )
        return _file(log, st, cfg, it, gate)


def settle(log: EventLog, st: State, cfg: Config, item: str, gate: str = "") -> list[str]:
    """Settle the follow-up task(s) bound to ``item`` (one gate's when ``gate`` is given)
    by removing them from the queue: the item is being abandoned or completed, or ``gate``
    has been re-recorded non-skipped, so the follow-up's only job is done elsewhere."""
    if cfg.gates.skip_followup == "off":
        return []
    ids = [i.id for i in _open(st, item, gate)]
    if not ids:
        return []
    why = (
        f"settled: its item {item} settled"
        if not gate
        else f"settled: {gate} re-recorded non-skipped on {item}"
    )
    for tid in ids:
        log.append("task.removed", tid, {"reason": why})
    return ids
