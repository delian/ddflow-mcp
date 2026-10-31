"""Work tracking: fold the event log into one ``ItemWork`` record per item."""

from __future__ import annotations

from ..events import Event
from ..model import GATE_OUTCOMES, State
from .models import Attempt, ItemWork, _epoch


def _absorb(ev: Event, rec, open_attempt: dict[str, Attempt]) -> None:
    """Fold one event into the work record.

    Split out of `work` because the dispatch is long and flat: each arm is independent
    and reading them interleaved with the setup obscured that none of them shares
    state with any other beyond the open-attempt table.
    """
    kind, subj, d = ev.kind, ev.subject, ev.data
    if kind in ("task.added", "phase.added"):
        r = rec(subj)
        r.first_seen = r.first_seen or ev.ts
    elif kind == "lease.acquired":
        r = rec(subj)
        att = Attempt(
            item=subj,
            holder=d.get("holder", ev.agent),
            started_at=_epoch(ev.ts) or float(d.get("at", 0.0)),
        )
        # A second acquire without an intervening release closes the first: that is a
        # re-claim, and counting it as one long attempt would hide exactly the
        # repetition this module exists to surface.
        if subj in open_attempt:
            prev = open_attempt[subj]
            prev.ended_at = att.started_at
            prev.ended_by = "reclaimed"
        open_attempt[subj] = att
        r.attempts.append(att)
        r.holders.append(att.holder)
        r.last_touched = ev.ts
    elif kind in ("lease.released", "lease.expired"):
        att = open_attempt.pop(subj, None)
        if att:
            att.ended_at = att.ended_at or _epoch(ev.ts)
            att.ended_by = "released" if kind.endswith("released") else "expired"
        rec(subj).last_touched = ev.ts
    elif kind == "gate.started":
        rec(subj).open_gate_round(str(d.get("gate") or "?"))
    elif kind.startswith("gate.") and kind.split(".", 1)[1] in GATE_OUTCOMES:
        # The membership test, not the prefix. `gate.` is a namespace and
        # `gate.out_of_order` is in it without being an outcome; matching the prefix
        # counted it as a gate run and inflated `gate_runs` for every item that ever
        # recorded one early.
        outcome = kind.split(".", 1)[1]
        r = rec(subj)
        r.add_round_verdict(str(d.get("gate") or "?"), outcome)
        # What the item's gates said is `Item.gate_history`, read in `work` once the pass
        # is over; only the attempt's own tallies are counted as events go by.
        att = open_attempt.get(subj)
        if att:
            att.gates_run += 1
            att.gates_passed += outcome == "passed"
            att.gates_failed += outcome == "failed"
        r.last_touched = ev.ts
    elif kind == "item.completed":
        r = rec(subj)
        r.completed_times += 1
        r.last_touched = ev.ts
        att = open_attempt.pop(subj, None)
        if att:
            att.ended_at = att.ended_at or _epoch(ev.ts)
            att.ended_by = "completed"
        if d.get("sha"):
            r.add_commit(d["sha"])
    elif kind == "item.abandoned":
        rec(subj).last_touched = ev.ts
        att = open_attempt.pop(subj, None)
        if att:
            att.ended_at = att.ended_at or _epoch(ev.ts)
            att.ended_by = "abandoned"
    elif kind == "worktree.merged" and d.get("sha"):
        rec(subj).add_commit(d["sha"])


def work(events: list[Event], state: State) -> dict[str, ItemWork]:
    """Aggregate every item's history from the log.

    A single forward pass. Attempts are opened by ``lease.acquired`` and closed by the
    next release/expiry/completion for that item, so an attempt that is still open has
    no ``ended_at`` and reports elapsed-to-now — which is what you want when asking
    "how long has this been held".
    """
    out: dict[str, ItemWork] = {}
    open_attempt: dict[str, Attempt] = {}

    def rec(item_id: str) -> ItemWork:
        if item_id not in out:
            it = state.items.get(item_id)
            out[item_id] = ItemWork(
                item=item_id,
                kind=it.kind if it else "task",
                title=it.title if it else "",
                state=it.state if it else "open",
            )
        return out[item_id]

    for ev in events:
        _absorb(ev, rec, open_attempt)

    for item_id, r in out.items():
        it = state.items.get(item_id)
        if it:
            r.state, r.kind, r.title = it.state, it.kind, it.title
            for run in it.gate_history:
                r.gate_outcomes.setdefault(run.gate or "?", []).append(run.outcome)
                r.add_gate_run(run.gate or "?", run.outcome, run.evidence)
    return out
