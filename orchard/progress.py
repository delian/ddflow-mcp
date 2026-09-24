"""Work actually done, and loops that never finish it.

Two questions this module answers from the event log alone, with no extra bookkeeping
for an agent to forget:

1. **What work has been done?** Attempts per item, wall-clock held, gates run, commits
   produced, who did it. The log already records every one of these; nothing aggregated
   them, so "how much effort has gone into P1.T3" had no answer short of reading JSONL.

2. **Is anything looping?** A dependency cycle is the easy case and is caught
   statically by ``schedule.find_cycles``. The expensive cases are *runtime* loops,
   where the graph is perfectly acyclic and the work still never finishes: an item
   claimed and released forever, a gate that passes and fails and passes again, a task
   completed and re-opened and completed again, or a whole queue where events keep
   arriving and no state ever advances.

**Why the log can answer this and a counter cannot.** A retry counter has to be
incremented by the thing doing the retrying, which is the thing that has lost track.
The log is written by every operation as a side effect of doing it, so a loop is
visible in it whether or not anyone thought to look.

**Every detector is bounded and evidence-carrying.** A finding names the item, the
count, the threshold it crossed and the timestamps, because "something is looping" is
not actionable and "P1.T3 has been claimed 5 times since 09:14 and never completed" is.
And every threshold is a knob: a project doing genuine exploratory work legitimately
re-claims items, and a detector that fires on healthy work is one people learn to
ignore.
"""

from __future__ import annotations

import itertools
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import Config
from .events import Event
from .model import ABANDONED, DONE, State

#: Kinds that represent forward progress. Used by the no-progress detector: a window
#: full of events none of which is one of these is a window in which nothing advanced.
PROGRESS_KINDS: frozenset[str] = frozenset(
    {
        "item.completed",
        "gate.passed",
        "worktree.merged",
        "task.added",
        "phase.added",
        "bug.fixed",
        "lesson.recorded",
        "research.recorded",
        "item.abandoned",
    }
)


def _epoch(ts: str) -> float:
    """Event timestamp -> epoch seconds. 0.0 when unparseable.

    Needed because only `lease.acquired` carries a numeric `at`; every other event
    dates itself with the ISO `ts`. Closing an attempt with `time.time()` instead
    reported elapsed-to-NOW for work that finished days ago, which silently inflated
    every duration in the report -- the numbers looked precise and were wrong.
    """
    if not ts:
        return 0.0
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class Attempt:
    """One claim-to-release span on an item."""

    item: str
    holder: str
    started_at: float = 0.0
    ended_at: float = 0.0
    ended_by: str = ""  # released | expired | completed | abandoned | (open)
    gates_run: int = 0
    gates_passed: int = 0
    gates_failed: int = 0

    @property
    def seconds(self) -> float:
        """Elapsed for this attempt; elapsed-to-now only while it is still open."""
        if not self.started_at:
            return 0.0
        return max(0.0, (self.ended_at or time.time()) - self.started_at)

    @property
    def open(self) -> bool:
        return not self.ended_by


@dataclass
class ItemWork:
    item: str
    kind: str = "task"
    title: str = ""
    state: str = "open"
    attempts: list[Attempt] = field(default_factory=list)
    gate_outcomes: dict[str, list[str]] = field(default_factory=dict)
    commits: list[str] = field(default_factory=list)
    holders: list[str] = field(default_factory=list)
    completed_times: int = 0
    first_seen: str = ""
    last_touched: str = ""

    @property
    def total_seconds(self) -> float:
        return sum(a.seconds for a in self.attempts)

    @property
    def gate_runs(self) -> int:
        return sum(len(v) for v in self.gate_outcomes.values())

    def summary(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "attempts": len(self.attempts),
            "held_seconds": round(self.total_seconds, 1),
            "gate_runs": self.gate_runs,
            "commits": len(self.commits),
            "holders": sorted(set(self.holders)),
            "completed_times": self.completed_times,
        }


@dataclass
class LoopFinding:
    """One detected loop. ``kind`` names the pattern; ``detail`` carries the evidence."""

    kind: str
    item: str
    count: int
    threshold: int
    detail: str
    severity: str = "warn"  # warn | block

    def render(self) -> str:
        return f"[{self.severity.upper()}] {self.kind} · {self.item}: {self.detail}"


# -- work tracking ---------------------------------------------------------------------


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
    elif kind.startswith("gate."):
        outcome = kind.split(".", 1)[1]
        if outcome == "started":
            return
        r = rec(subj)
        r.gate_outcomes.setdefault(d.get("gate", "?"), []).append(outcome)
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
            r.commits.append(d["sha"])
    elif kind == "item.abandoned":
        rec(subj).last_touched = ev.ts
        att = open_attempt.pop(subj, None)
        if att:
            att.ended_at = att.ended_at or _epoch(ev.ts)
            att.ended_by = "abandoned"
    elif kind == "worktree.merged" and d.get("sha"):
        rec(subj).commits.append(d["sha"])


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
    return out


# -- loop detection ----------------------------------------------------------------------


def _is_removed(state: State, item_id: str) -> bool:
    it = state.items.get(item_id)
    return bool(it and it.removed)


def detect(events: list[Event], state: State, cfg: Config) -> list[LoopFinding]:
    """Every loop pattern the log can show, each with its evidence.

    Deliberately several narrow detectors rather than one clever one: each names a
    distinct failure with a distinct remedy, and a single "looping" verdict would leave
    the reader to work out which.
    """
    lc = cfg.loops
    found: list[LoopFinding] = []
    # Removed items are dropped once, here, rather than in each detector. Three of the
    # six filtered on state alone, so an item taken out of the queue kept generating a
    # "claimed and given up 3 times" warning whose suggested remedy — abandon it — had
    # already been done in a stronger form; under `[loops] on_detect = "block"` that is
    # a permanent block on work nobody is doing. `_static_cycles` and `_duplicate_work`
    # already filtered `removed`, and that inconsistency was the tell.
    tracked = {k: v for k, v in work(events, state).items() if not _is_removed(state, k)}
    sev = "block" if lc.on_detect == "block" else "warn"

    found += _static_cycles(state, sev)
    found += _repeat_claims(tracked, lc, sev)
    found += _gate_flapping(tracked, lc, sev)
    found += _reopened(tracked, lc, sev)
    found += _duplicate_work(state, lc, sev)
    found += _no_progress(events, lc, sev)
    return found


def _static_cycles(state: State, sev: str) -> list[LoopFinding]:
    from .schedule import find_cycles

    items = {i.id: i for i in state.items.values() if not i.removed}
    out = []
    for cyc in find_cycles(items):
        out.append(
            LoopFinding(
                kind="dependency_cycle",
                item=cyc[0],
                count=len(cyc) - 1,
                threshold=0,
                severity="block",
                detail=(
                    f"{' -> '.join(cyc)}. Nothing in this ring can ever start: every "
                    f"member waits on another member. Break it by removing one "
                    f"`needs` edge (`orchard update <id> --needs ...`)."
                ),
            )
        )
    return out


def _repeat_claims(tracked: dict[str, ItemWork], lc, sev: str) -> list[LoopFinding]:
    """The commonest runtime loop: claim, fail, release, claim again, forever."""
    out = []
    for r in tracked.values():
        if r.state in (DONE, ABANDONED):
            continue
        unfinished = [a for a in r.attempts if a.ended_by != "completed"]
        if len(unfinished) < lc.max_claims_per_item:
            continue
        # Expiries are crash recovery, not thrashing -- a crashed agent is a different
        # problem with a different remedy, and counting it here would blame the wrong
        # thing. Only deliberate release/re-claim cycles count.
        deliberate = [a for a in unfinished if a.ended_by in ("released", "reclaimed")]
        if len(deliberate) < lc.max_claims_per_item:
            continue
        holders = Counter(a.holder for a in deliberate)
        out.append(
            LoopFinding(
                kind="repeat_claims",
                item=r.item,
                count=len(deliberate),
                threshold=lc.max_claims_per_item,
                severity=sev,
                detail=(
                    f"claimed and given up {len(deliberate)} times without completing "
                    f"(holders: {', '.join(f'{h}x{n}' for h, n in holders.most_common())}; "
                    f"{r.gate_runs} gate runs, {len(r.commits)} commits). Either the "
                    f"task is underspecified, or a gate it cannot pass is blocking it. "
                    f"Split it, or `orchard abandon {r.item} --reason ...`."
                ),
            )
        )
    return out


def _gate_flapping(tracked: dict[str, ItemWork], lc, sev: str) -> list[LoopFinding]:
    """A gate that keeps changing its mind. Usually a flaky test or a moving target."""
    out = []
    for r in tracked.values():
        for gate, outcomes in r.gate_outcomes.items():
            decisive = [o for o in outcomes if o in ("passed", "failed")]
            flips = sum(1 for a, b in itertools.pairwise(decisive) if a != b)
            if flips < lc.max_gate_flaps:
                continue
            out.append(
                LoopFinding(
                    kind="gate_flapping",
                    item=r.item,
                    count=flips,
                    threshold=lc.max_gate_flaps,
                    severity=sev,
                    detail=(
                        f"gate {gate!r} changed verdict {flips} times "
                        f"({' -> '.join(decisive[-6:])}). A gate that cannot decide is "
                        f"either flaky or measuring something that keeps moving; "
                        f"re-running it will not converge."
                    ),
                )
            )
    return out


def _reopened(tracked: dict[str, ItemWork], lc, sev: str) -> list[LoopFinding]:
    """Completed, then picked up again, more than once. Work that will not stay done."""
    out = []
    for r in tracked.values():
        if r.completed_times < lc.max_reopens:
            continue
        out.append(
            LoopFinding(
                kind="reopened",
                item=r.item,
                count=r.completed_times,
                threshold=lc.max_reopens,
                severity=sev,
                detail=(
                    f"completed {r.completed_times} times. Work that will not stay done "
                    f"usually means the acceptance criteria are not in the item, so "
                    f"each pass 'finishes' something different. Put them in the body "
                    f"(`orchard update {r.item} --body ...`) before the next attempt."
                ),
            )
        )
    return out


def _duplicate_work(state: State, lc, sev: str) -> list[LoopFinding]:
    """Two live items that write the same files. A queue re-describing its own work."""
    out = []
    by_globs: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for it in state.items.values():
        if it.removed or it.state in (DONE, ABANDONED) or not it.globs:
            continue
        by_globs[tuple(sorted(it.globs))].append(it.id)
    for globs, ids in by_globs.items():
        if len(ids) < lc.max_duplicate_items:
            continue
        out.append(
            LoopFinding(
                kind="duplicate_work",
                item=sorted(ids)[0],
                count=len(ids),
                threshold=lc.max_duplicate_items,
                severity=sev,
                detail=(
                    f"{len(ids)} open items declare exactly the same files "
                    f"({', '.join(globs)}): {', '.join(sorted(ids))}. They cannot run "
                    f"in parallel (the conflict detector will refuse), and one of them "
                    f"is probably a re-description of another."
                ),
            )
        )
    return out


def _no_progress(events: list[Event], lc, sev: str) -> list[LoopFinding]:
    """Events keep arriving and nothing ever advances. The whole-queue livelock.

    Looks only at the tail: a project that stalled last month and then recovered is
    not stalled. The window is in EVENTS rather than minutes because an agent that is
    thinking produces no events at all, and waiting is not looping.
    """
    window = events[-lc.no_progress_window :]
    if len(window) < lc.no_progress_window:
        return []
    if any(e.kind in PROGRESS_KINDS for e in window):
        return []
    busiest = Counter(e.subject for e in window).most_common(3)
    return [
        LoopFinding(
            kind="no_progress",
            item=busiest[0][0] if busiest else "(queue)",
            count=len(window),
            threshold=lc.no_progress_window,
            severity=sev,
            detail=(
                f"the last {len(window)} events produced no completion, no gate pass "
                f"and no merge. Busiest subjects: "
                f"{', '.join(f'{s} ({n})' for s, n in busiest)}. The queue is moving "
                f"without advancing — stop and re-plan rather than continuing."
            ),
        )
    ]
