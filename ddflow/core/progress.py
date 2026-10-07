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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..core.model import (
    ABANDONED,
    BLOCKED,
    DONE,
    GATE_OUTCOMES,
    OPEN,
    REVIEW,
    RUNNING,
    Item,
    State,
)
from . import clock
from .events import Event
from .graph import closure

#: Gates whose failure is a reviewer's verdict on the work, not a failure of the work
#: (decision D-failed-critic-not-blocking). The repeated-failure detector skips them.
REVIEW_GATES: frozenset[str] = frozenset({"rubber_duck", "critic"})

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
        "bug.invalid",
        "lesson.recorded",
        "research.recorded",
        "item.abandoned",
    }
)


def epoch(ts: str) -> float:
    """Public name for `_epoch`: event timestamp -> epoch seconds, 0.0 when unparseable."""
    return _epoch(ts)


def _epoch(ts: str) -> float:
    """Event timestamp -> epoch seconds. 0.0 when unparseable.

    Needed because only `lease.acquired` carries a numeric `at`; every other event
    dates itself with the ISO `ts`. Closing an attempt with `time.time()` instead
    reported elapsed-to-NOW for work that finished days ago, which silently inflated
    every duration in the report -- the numbers looked precise and were wrong.
    """
    # A zone-less time is the host's clock here, as it always was (B-uni-clock keeps
    # each caller's reading; the log itself never writes one).
    return clock.epoch(ts, naive="local", default=0.0)


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
    #: gate -> (outcome, output digest, tree fingerprint) of each passed/failed run, in
    #: order. What the repeated-failure detector reads; "" where a run recorded none.
    gate_runs_evidence: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    commits: list[str] = field(default_factory=list)
    holders: list[str] = field(default_factory=list)
    completed_times: int = 0
    first_seen: str = ""
    last_touched: str = ""

    def add_commit(self, sha: str) -> None:
        """Record a commit once: merge and complete both name the landing's sha."""
        if sha not in self.commits:
            self.commits.append(sha)

    def add_gate_run(self, gate: str, outcome: str, evidence: Any) -> None:
        """Keep a decisive run's output digest and tree, for the repeated-failure check."""
        if outcome not in ("passed", "failed"):
            return
        ev = evidence if isinstance(evidence, dict) else {}
        self.gate_runs_evidence.setdefault(gate, []).append(
            (outcome, str(ev.get("output_digest") or ""), str(ev.get("tree_sha") or ""))
        )

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
    #: The gate and the tree it last failed on, for the findings that are about one gate.
    gate: str = ""
    tree_sha: str = ""

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
    elif kind.startswith("gate.") and kind.split(".", 1)[1] in GATE_OUTCOMES:
        # The membership test, not the prefix. `gate.` is a namespace and
        # `gate.out_of_order` is in it without being an outcome; matching the prefix
        # counted it as a gate run and inflated `gate_runs` for every item that ever
        # recorded one early.
        outcome = kind.split(".", 1)[1]
        if outcome == "started":
            return
        r = rec(subj)
        r.gate_outcomes.setdefault(d.get("gate", "?"), []).append(outcome)
        r.add_gate_run(d.get("gate", "?"), outcome, d.get("evidence"))
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
    found += _repeated_failures(tracked, lc, sev)
    found += _reopened(tracked, lc, sev)
    found += _duplicate_work(state, lc, sev)
    found += _no_progress(events, lc, sev)
    return found


def _static_cycles(state: State, sev: str) -> list[LoopFinding]:
    from ..core.schedule import find_cycles

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
                    f"`needs` edge (`ddflow update <id> --needs ...`)."
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
                    f"Split it, or `ddflow abandon {r.item} --reason ...`."
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


def _repeated_failures(tracked: dict[str, ItemWork], lc, sev: str) -> list[LoopFinding]:
    """One gate failing the same way again and again: N consecutive failed runs with one
    output digest. `gate_flapping` needs the verdict to flip; an agent re-applying the
    same failing patch never flips it. A pass ends the streak, and so does a failure
    that recorded no digest (nothing shows it failed the same way). Reviewer gates are
    excluded -- a failed review is not a failure of the work."""
    n = lc.max_repeated_failures
    if n <= 0:
        return []
    out = []
    for r in tracked.values():
        for gate, runs in r.gate_runs_evidence.items():
            if gate in REVIEW_GATES:
                continue
            outcome, digest, tree = runs[-1]
            if outcome != "failed" or not digest:
                continue
            streak = 0
            for o, dg, _ in reversed(runs):
                if o != "failed" or dg != digest:
                    break
                streak += 1
            if streak < n:
                continue
            out.append(
                LoopFinding(
                    kind="repeated_failure",
                    item=r.item,
                    count=streak,
                    threshold=n,
                    severity=sev,
                    gate=gate,
                    tree_sha=tree,
                    detail=(
                        f"gate {gate!r} has failed {streak} times in a row with the same "
                        f"output (digest {digest}). Re-running it on the same work will "
                        f"not pass: read the failure (`ddflow gate status {r.item}`), "
                        f"change something, or `ddflow block {r.item}` / ask for help."
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
                    f"(`ddflow update {r.item} --body ...`) before the next attempt."
                ),
            )
        )
    return out


def _duplicate_work(state: State, lc, sev: str) -> list[LoopFinding]:
    """Two live items that declare exactly the same files: a conflict, not a duplicate.

    Identical globs say the items cannot run at once. They do not say the work is the
    same (Bf84cccbaa6: three distinct features shared coarse imported globs), so the
    finding asks for a comparison rather than asserting a re-description.

    A pair that `needs` already orders is not a finding (Bd1c601896f): the scheduler
    will never hand both out at once, so "they cannot run in parallel" asks the
    operator to act on nothing. Only members of some UNORDERED pair are named.
    """
    out = []
    by_globs: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for it in state.items.values():
        if it.removed or it.state in (DONE, ABANDONED) or not it.globs:
            continue
        by_globs[tuple(sorted(it.globs))].append(it.id)
    waits: dict[str, set[str]] = {}
    for globs, ids in by_globs.items():
        if len(ids) < lc.max_duplicate_items:
            continue
        for i in ids:
            if i not in waits:
                waits[i] = _waits_on(state, i)
        loose = sorted(
            {
                x
                for a, b in itertools.combinations(ids, 2)
                if b not in waits[a] and a not in waits[b]
                for x in (a, b)
            }
        )
        # The threshold was met by the group above; ordering may only REMOVE a
        # finding, so it is not re-applied to the unordered members.
        if not loose:
            continue
        ordered = sorted(set(ids) - set(loose))
        aside = (
            f" ({', '.join(ordered)} {'is' if len(ordered) == 1 else 'are'} already "
            f"ordered against the others by `needs`, so not listed.)"
            if ordered
            else ""
        )
        out.append(
            LoopFinding(
                kind="duplicate_work",
                item=loose[0],
                count=len(loose),
                threshold=lc.max_duplicate_items,
                severity=sev,
                detail=(
                    f"{len(loose)} open items declare exactly the same files "
                    f"({', '.join(globs)}): {', '.join(loose)}.{aside} They cannot run "
                    f"in parallel (the conflict detector will refuse), and no `needs` "
                    f"chain orders them. Sharing files says nothing about whether the "
                    f"work is the same: compare their titles and bodies, narrow the "
                    f"globs if they are coarser than the work, add a `needs` edge if "
                    f"one must follow the other, and `ddflow remove <id>` only if one "
                    f"really re-describes another."
                ),
            )
        )
    return out


def _waits_on(state: State, item_id: str) -> set[str]:
    """Every live item whose work must come before ``item_id``'s, transitively.

    The graph readiness uses, not a re-derived one: ``inherited_deps`` (an item's own
    `needs` plus its ancestors'), and a dependency on an item also waits for everything
    beneath it, since a phase cannot finish while a task in it is open. The walk stops
    at a dependency that cannot sequence anything: one that is done, removed or in
    another repository holds nothing back, and one that is abandoned or unknown holds
    back forever -- neither puts one item's work after the other's. A dependency in
    REVIEW still orders, even when `flow.stack` lets the dependent start: its work is
    finished and the dependent forks from it. Iterative with a visited set: a `needs`
    cycle (reported by ``_static_cycles``) must not hang it.
    """
    from ..core.schedule import inherited_deps

    def live(i: str) -> bool:
        it = state.items.get(i)
        return bool(it and not it.removed and it.state not in (DONE, ABANDONED))

    def waits(i: str) -> list[str]:
        out: list[str] = []
        for _owner, dep in inherited_deps(state, state.items[i]):
            if live(dep):
                out += [n for n in (dep, *sorted(state.descendants(dep))) if live(n)]
        return out

    return set(closure(item_id, waits))


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


# -- Tallies: how far along a phase or the queue is, counted once -----------------------
#
# Phase done/total used to be computed five ways (B-uni-tallies): `list phase` kept
# abandoned tasks in the total (B01281f654e), the progress line counted direct children
# only and missed every sub-task (Bb24939611d), and the board, the roadmap and the views
# each re-derived it. ONE rule now, pinned by tests/test_tallies.py:
#
# - a phase's tasks are every live (not removed) task nested ANY depth below it, sub-tasks
#   and bug-fix (`fixes`) tasks included: a phase cannot close while any of them is open;
# - an abandoned task is SETTLED, not outstanding: it is out of the total and counted on
#   its own, so a phase whose remaining work is all done reads n/n, not n/n+1.
#
# The queue-wide "tasks" figure of the progress line leaves `fixes` tasks out because it
# shows them as "bugs fixed"; that is a separate number with its own label, not a phase
# tally.

#: Task states in the order a per-state count lists them.
STATE_ORDER: tuple[str, ...] = (DONE, RUNNING, REVIEW, OPEN, BLOCKED, ABANDONED)


@dataclass(frozen=True)
class Tally:
    """``done`` of ``live`` tasks finished; ``abandoned`` settled outside the total."""

    done: int = 0
    live: int = 0
    abandoned: int = 0

    @property
    def complete(self) -> bool:
        """Every live task is done, and there is at least one."""
        return self.live > 0 and self.done == self.live


def tally(tasks: Iterable[Item]) -> Tally:
    """The tally of these tasks, under the one rule above."""
    done = live = abandoned = 0
    for t in tasks:
        if t.state == ABANDONED:
            abandoned += 1
            continue
        live += 1
        done += t.state == DONE
    return Tally(done, live, abandoned)


def phase_tally(st: State, phase: str) -> Tally:
    """How far along ``phase`` is: its live tasks at every depth."""
    return tally(st.tasks(phase))


def phase_of(st: State, item_id: str) -> str:
    """The phase ``item_id`` belongs to: itself when it is one, else its nearest phase
    ancestor (a sub-task's PARENT is a task, not its phase: B45b55be6a4); "" for none."""
    it = st.items.get(item_id)
    if it is None:
        return ""
    if it.kind == "phase":
        return it.id
    return next((a.id for a in st.ancestors(item_id) if a.kind == "phase"), "")


def state_counts(tasks: Iterable[Item]) -> dict[str, int]:
    """How many of ``tasks`` are in each state, every state of ``STATE_ORDER`` present
    (zero included) and in that order; a state outside it follows, by name."""
    seen = Counter(t.state for t in tasks)
    out = {s: seen.pop(s, 0) for s in STATE_ORDER}
    out.update(sorted(seen.items()))
    return out


# -- Board rows: the board's structure, built once for the markdown and the JSON ---------


def depth(st: State, item: Item, root: str) -> int:
    """How far below ``root`` this item sits. Bounded, because a parent chain is
    operator-authored and a cycle in it must not hang the renderer."""

    def parent(i: str) -> list[str]:
        up = (item if i == item.id else st.items[i]).parent
        return [up] if up and up != root and up in st.items else []

    above = [n for n in closure(item.id, parent) if n != item.id]
    # A chain that ends in an item that is its own parent counts that last step once.
    last = st.items[above[-1]] if above else item
    return min(len(above) + (last.parent == last.id != root), 6)


def nested(st: State, phase: str, tasks: list | None = None) -> list:
    """Tasks under a phase, each sub-task immediately after its parent. ``tasks``
    overrides the set (the unphased ones, whose root is the empty id)."""
    if tasks is None:
        tasks = st.tasks(phase)
    by_parent: dict[str, list] = {}
    for t in tasks:
        by_parent.setdefault(t.parent, []).append(t)
    out: list = []

    def walk(parent: str, seen: set) -> None:
        for t in sorted(by_parent.get(parent, []), key=lambda x: (x.priority, x.id)):
            if t.id in seen:
                continue
            seen.add(t.id)
            out.append(t)
            walk(t.id, seen)

    walk(phase, set())
    # Anything whose parent chain does not reach the phase (an orphan, or a cycle)
    # still belongs on the board: silently dropping it is how work disappears.
    listed = {t.id for t in out}
    out.extend(t for t in tasks if t.id not in listed)
    return out


def unphased(st: State) -> list:
    """Live tasks that sit under no phase, a sub-task after its parent (Bc896ea5d16: the
    board listed only phases' tasks, so a task added with no phase never appeared)."""
    under: set[str] = set()
    for ph in st.phases():
        under |= set(st.descendants(ph.id))
    return nested(st, "", [t for t in st.tasks() if t.id not in under])


@dataclass(frozen=True)
class BoardSection:
    """One section of the board: a phase (``phase`` None for the unphased tasks), its
    tally, and its task rows in board order."""

    phase: Item | None
    tally: Tally
    rows: list[dict[str, Any]]


def board_rows(
    st: State, pipeline: Callable[[Item], list[str]], phase: str = ""
) -> list[BoardSection]:
    """The board's sections, in board order: each phase (only ``phase`` when one is
    named) by (priority, id), then the unphased tasks when no phase is named and there
    are any. ``pipeline(item)`` is the item's configured gate pipeline (core does not
    read the config: the caller hands it ``services.gates.pipeline_for``)."""

    def rows(tasks: list, root: str) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id,
                "title": t.title,
                "state": t.state,
                "parent": t.parent,
                "depth": depth(st, t, root),
                "needs": list(t.needs),
                "globs": list(t.globs),
                "owner": t.lease.holder if t.lease else "",
                "gates": {g: t.gate_outcome(g) for g in pipeline(t)},
            }
            for t in tasks
        ]

    out = [
        BoardSection(ph, tally(tasks), rows(tasks, ph.id))
        for ph in sorted(st.phases(), key=lambda p: (p.priority, p.id))
        if not phase or ph.id == phase
        for tasks in [nested(st, ph.id)]
    ]
    loose = [] if phase else unphased(st)
    if loose:
        out.append(BoardSection(None, tally(loose), rows(loose, "")))
    return out
