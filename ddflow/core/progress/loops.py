"""Loop detection: every runtime loop the event log can show, each with its evidence."""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict

from ...config import Config
from ..events import Event
from ..graph import closure, find_cycles
from ..model import ABANDONED, DONE, State
from ..schedule import inherited_deps
from .models import BOOKKEEPING_KINDS, PROGRESS_KINDS, REVIEW_GATES, ItemWork, LoopFinding
from .tracking import work


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
    found += _duplicate_work(state, lc, sev, cfg)
    found += _no_progress(events, lc, sev)
    return found


def _static_cycles(state: State, sev: str) -> list[LoopFinding]:
    items = state.live_by_id()
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
        for gate, rounds in r.gate_rounds.items():
            # A round's verdict is the LAST decisive one before the next round starts: a
            # failed review round settled by its own triage is one `passed` round, not a
            # flip (docs/ddflow/research/gate-flapping.md).
            decisive = [rd[-1] for rd in rounds if rd]
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


def _duplicate_work(state: State, lc, sev: str, cfg: Config | None = None) -> list[LoopFinding]:
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
        if it.removed or it.terminal or not it.globs:
            continue
        by_globs[tuple(sorted(it.globs))].append(it.id)
    waits: dict[str, set[str]] = {}
    for globs, ids in by_globs.items():
        if len(ids) < lc.max_duplicate_items:
            continue
        for i in ids:
            if i not in waits:
                waits[i] = _waits_on(state, i, cfg)
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


def _waits_on(state: State, item_id: str, cfg: Config | None = None) -> set[str]:
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

    def live(i: str) -> bool:
        it = state.items.get(i)
        return bool(it and not it.removed and not it.terminal)

    def waits(i: str) -> list[str]:
        out: list[str] = []
        for _owner, dep in inherited_deps(state, state.items[i], cfg):
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
    counted = [e for e in events if e.kind not in BOOKKEEPING_KINDS]
    window = counted[-lc.no_progress_window :]
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
