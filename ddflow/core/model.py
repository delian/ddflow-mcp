"""Domain model and the fold — the deterministic projection of the event log.

``fold(events) -> State`` is a pure function. Same events in, same state out, on any
machine, in any process, at any time. Every rule in this module is therefore testable
without touching a disk, and ``ddflow rebuild`` is just ``fold`` over everything.

The hierarchy is deliberately two levels plus a grouping:

    Plan  ->  Phase  ->  Task

A **Phase** is a unit of *review* — it has its own research, its own whole-phase test
pass, its own live smoke run, and it merges as a coherent feature. A **Task** is a unit
of *execution* — one agent, one worktree, one pipeline, one merge. Both carry
``needs`` (dependencies), so a task may depend on a task in another phase and a phase
may depend on another phase. Anything deeper than this (epics, sub-sub-tasks) is
representable by making a phase's task depend on another's, and the extra level costs
more in bookkeeping than it buys.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .events import Event

# Item states. These are DERIVED, never written: an item's state is a function of the
# events about it. A state field that can be set directly is a field that can drift
# from the events that produced it, which is the whole defect class this design closes.
OPEN, RUNNING, BLOCKED, DONE, ABANDONED = "open", "running", "blocked", "done", "abandoned"
#: Waiting on a pull request (RESEARCH R16). Distinct from RUNNING because nobody holds
#: it: the lease is released when the request opens, so the agent can take the next
#: task. As RUNNING-without-a-lease it would read as a crash to `recover` and as
#: interrupted work to `next`, and every parked review would be offered to a second agent.
REVIEW = "review"

#: The single source for gate outcomes. Everything that validates, renders or maps an
#: outcome imports from here. Previously this tuple existed and nothing referenced it,
#: while five scattered literals did the real work -- so adding a sixth outcome meant
#: finding all five. A dead constant that LOOKS canonical is worse than none at all.
#: The outcomes a gate can be RECORDED with. `started` is deliberately absent: it is an
#: event kind in the `gate.` namespace, not an outcome, and anything that accepts it as
#: one lets a caller record a gate as having begun and never finished.
#:
#: `gate.` is a NAMESPACE, not a synonym for "an outcome was recorded" --
#: `gate.out_of_order` lives there too and is neither. A consumer matching the PREFIX
#: counted that as a gate run, which is how adding one event kind silently changed a
#: metric two modules away.
GATE_OUTCOMES: tuple[str, ...] = ("passed", "failed", "unavailable", "partial", "skipped")

#: Outcome -> single-character mark, used by both the board and the gate status view.
OUTCOME_MARK: dict[str, str] = {
    "passed": "x",
    "failed": "!",
    "unavailable": "?",
    "partial": "~",
    "skipped": "-",
    "": " ",
}


@dataclass
class GateRecord:
    """One gate's outcome for one item.

    ``unavailable`` is a first-class outcome, distinct from both pass and fail. A
    reviewer that could not run is not a reviewer that found nothing; recording the
    two the same way is how a whole review silently disappears from a pipeline.
    """

    gate: str
    outcome: str = ""
    at: str = ""
    by: str = ""
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking_failure(self) -> bool:
        return self.outcome in ("failed",)


@dataclass
class Lease:
    holder: str
    acquired_at: float
    renewed_at: float
    ttl_s: int
    worktree: str = ""
    branch: str = ""
    globs: list[str] = field(default_factory=list)
    note: str = ""
    #: Physical resources this lease holds (`gpu:4`, `vllm-fleet`). See
    #: `schedule.resource_shortfall`.
    resources: list[str] = field(default_factory=list)
    #: Set when a `lease.expired` event was folded. The lease is KEPT so recovery can
    #: still see which worktree it pointed at.
    expired_at: str = ""
    #: The `lease.acquired` event that granted it, so a lease contest can name the claims.
    event: str = ""

    def expired(self, now: float, grace_s: int = 0) -> bool:
        return (now - self.renewed_at) > (self.ttl_s + grace_s)

    def remaining_s(self, now: float) -> float:
        return (self.renewed_at + self.ttl_s) - now


@dataclass
class PullRequest:
    """The forge's pull/merge request for an item, as last observed.

    A SNAPSHOT, written by `pr.*` events, never polled at fold time: `fold` is pure, and
    the forge is the one thing in this system that changes without an event. So the
    projection can be stale, and says when it was last looked at (`synced_at`) instead
    of pretending otherwise.
    """

    number: int = 0
    url: str = ""
    forge: str = ""
    base: str = ""  # the branch it merges INTO -- a dependency's branch while stacked
    head: str = ""
    state: str = "open"  # open | merged | closed
    review: str = ""  # approved | changes_requested | pending | ""
    checks: str = ""  # passing | failing | pending | ""
    head_sha: str = ""
    merge_sha: str = ""
    feedback: str = ""
    #: The AUTHOR's model, given at `merge`. Completion runs at `pr sync`, possibly in
    #: another session by another agent, and the reviewer-independence check needs the
    #: author's family -- which that later caller does not know.
    author_model: str = ""
    synced_at: str = ""
    #: The head a change request was recorded against. A forge keeps saying "changes
    #: requested" until the reviewer looks again, so after a push the same request must
    #: not send the item back a second time.
    requested_head: str = ""
    #: How many times it went back for changes. Read by the loop detector's reader
    #: (`pr status`), because a request that bounces five times is not converging.
    rounds: int = 0


@dataclass
class Release:
    """One version tag ddflow cut."""

    version: str
    tag: str = ""
    sha: str = ""
    branch: str = ""
    items: list[str] = field(default_factory=list)
    at: str = ""
    pushed: bool = False
    line: str = ""


@dataclass
class Item:
    """A phase or a task. One class, because every rule about scheduling,
    leasing, gating and recovery is identical for both — only the pipeline differs.
    Two near-identical classes would drift; this repo's own history is full of that."""

    id: str
    kind: str  # "phase" | "task"
    title: str = ""
    parent: str = ""  # a task's phase; "" for a phase
    needs: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    #: What the work RUNS on, beside what it writes: `gpu:4`, `vllm-fleet`. Globs keep two
    #: agents out of one file; nothing kept two agents from both starting an 8-GPU run
    #: on an 8-GPU box. Counted against `[schedule] resources` capacities.
    resources: list[str] = field(default_factory=list)
    body: str = ""
    tags: list[str] = field(default_factory=list)
    priority: int = 100
    state: str = OPEN
    lease: Lease | None = None
    gates: dict[str, GateRecord] = field(default_factory=dict)
    worktree: str = ""
    branch: str = ""
    #: True when the worktree was ADOPTED -- the agent's harness created it and ddflow
    #: merely bound the item to it. `merge` must NOT delete one: it is not ours, and the
    #: harness may still be working in it. Recorded on the ITEM because `remove_on_merge`
    #: runs long after the claim, in another process, with only the fold to go on -- the
    #: `created=False` flag on the in-memory `Worktree` never survived that gap, so the
    #: safety property the adoption commit claimed was never actually implemented.
    adopted: bool = False
    merged_sha: str = ""
    #: The branch the worktree forked from. Recorded because under gitflow and stacking it
    #: is no longer "the base branch" -- a hotfix forks from production, a stacked task
    #: from its dependency's branch -- and the merge target is derived from it.
    base: str = ""
    pr: PullRequest | None = None
    #: The release line this item lands on ("" = inherit from its parent, else the
    #: current line). See `core.flow.effective_line`.
    line: str = ""
    #: Set on a generated PORT: the fix it carries (`port_of`), the item whose landing it
    #: ports (`port_from` -- the fix itself for cherry-pick, the previous line's port for
    #: forward-merge), and how. Frozen at creation, so changing `port_strategy` later
    #: does not reinterpret ports already in the queue.
    port_of: str = ""
    port_from: str = ""
    port_strategy: str = ""
    #: Set on a PROMOTION: the environment branch it moves work FROM and the one it lands
    #: ON -- always adjacent in [flow].environments, upstream to downstream.
    promote_from: str = ""
    promote_to: str = ""
    #: What the port did when it was applied: {"status": clean|conflict|failed, ...}.
    port: dict[str, Any] = field(default_factory=dict)
    #: The target branch just before and just after this item landed. The difference is
    #: exactly what landed, whatever the merge strategy -- which is what a cherry-pick
    #: port applies elsewhere.
    landed_before: str = ""
    landed_after: str = ""
    blocked_reason: str = ""
    created_at: str = ""
    completed_at: str = ""
    removed: bool = False
    #: Where this item came from, when it was not created by hand: `docs/todo.md:41`,
    #: `git:feature/x`. Empty for an item someone typed.
    #:
    #: A FIELD, not prose. The body already says "Imported from docs/todo.md:41." and a
    #: human reads that in `ddflow show` -- but "which items came from the import" is a
    #: question a machine has to answer for `import --verify`, and answering it by
    #: regexing a sentence is the metadata-key-vs-field class: the day someone rewords
    #: the sentence, the count silently becomes zero and the verification passes.
    source: str = ""
    #: How a DONE item was shown to be done, when nobody ran its gates: "ticked in
    #: docs/todo.md:41". ddflow does not invent completion, so when it records some it
    #: records who said so.
    completion_evidence: str = ""
    #: B191. Rival DEFINITIONS: `<kind>.added` events for this id from different clones,
    #: each {"event", "agent", "lamport", "ts", "title", "body", "data"}. One log refuses
    #: a second add of a live id, so a rival can only arrive by a merge -- and the fold
    #: used to let the later lamport silently win. Empty unless contested; the displayed
    #: fields are still the last-folded add's, so the fold stays deterministic.
    contested: list[dict[str, Any]] = field(default_factory=list)
    #: B191. Rival CLAIMS: a `lease.acquired` by another holder while the current lease
    #: was live, which only an offline claim in another clone can produce. Each is
    #: {"holder", "event", "lease"}; `lease` is the claim, re-applied if its holder is
    #: kept.
    lease_contest: list[dict[str, Any]] = field(default_factory=list)
    #: Every lease taken over by TTL arithmetic alone -- no release, no recorded expiry
    #: -- each a `lease_contest` entry plus "by": the claim that displaced it. Fold order
    #: is Lamport order, not wall time, so a displaced holder's renewals from another
    #: clone can fold AFTER the takeover, even several takeovers later, and they are the
    #: only evidence that it was live when the other claimed. `_late_renewal` weighs them
    #: against the entry. Bounded by `MAX_DISPLACED`.
    displaced: list[dict[str, Any]] = field(default_factory=list)

    def gate_outcome(self, gate: str) -> str:
        rec = self.gates.get(gate)
        return rec.outcome if rec else ""

    def contest_summary(self) -> str:
        """What is contested about this item, naming the rivals; "" when nothing is.

        One sentence, shared by the scheduler's refusal and doctor's problem, so the two
        cannot describe one contest differently.
        """
        parts = []
        if self.contested:
            parts.append(
                f"{len(self.contested)} definitions from different clones ("
                + "; ".join(
                    f"{d['event'][:12]} by {d['agent']}: {d['title']!r}" for d in self.contested
                )
                + ")"
            )
        if self.lease_contest:
            # Pairwise, never "claimed at once by" all of them: a contest is every claim
            # that overlapped ANOTHER, and two of them may never have met.
            named: set[str] = set()

            def who(h: dict[str, Any]) -> str:
                label = f"{h['holder']} ({h['event'][:12]}"
                if h["event"] not in named and "overlapped_by" in h:
                    label += f", still live when {h['overlapped_by']} claimed"
                named.add(h["event"])
                return label + ")"

            clauses = []
            for i, h in enumerate(self.lease_contest):
                met = _clashing(self.lease_contest[:i], h)
                if met:
                    clauses.append(f"{who(h)} overlapped " + " and ".join(who(g) for g in met))
                elif not _clashing(self.lease_contest, h):
                    clauses.append(f"{who(h)} overlapped none of them")
            parts.append("lease claims that overlapped: " + "; ".join(clauses))
        return "; ".join(parts)

    def lease_clashes(self, claim: dict[str, Any]) -> list[dict[str, Any]]:
        """The contestants whose windows overlapped ``claim``'s: the claims it met."""
        return _clashing(self.lease_contest, claim)

    def lease_losers(self, kept: dict[str, Any]) -> list[dict[str, Any]]:
        """The claims `resolve` releases when ``kept`` wins: every one that overlapped it,
        and the displayed lease if that is another claim -- ``kept`` is about to take the
        item from it. A contestant that met neither lost nothing to ``kept``."""
        losers = self.lease_clashes(kept)
        cur = self.lease
        if cur is not None and cur.event != kept["event"]:
            if all(h["event"] != cur.event for h in losers):
                losers.append(_claim(cur))
        return losers


@dataclass
class Bug:
    id: str
    item: str = ""
    summary: str = ""
    found_at: str = ""
    fixed_at: str = ""
    regression_test: str = ""
    lesson: str = ""

    @property
    def open(self) -> bool:
        return not self.fixed_at


@dataclass
class Lesson:
    id: str
    title: str = ""
    rule: str = ""
    why: str = ""
    how: str = ""
    seen_in: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    at: str = ""
    superseded_by: str = ""
    #: The lesson in one paragraph: what a lessons SUMMARY is made of. Both projects this
    #: was built against keep one beside their corpus -- one generated from each entry's
    #: `**Compressed:**` paragraph, one written by hand -- and a queue with no field for
    #: it could only offer the full rule, which is the thing a summary exists to avoid.
    summary: str = ""
    #: B20. The pattern this lesson forbids, and WHICH sites it currently occurs at --
    #: never how many. A count says "worse" and never "which", so it cannot be acted on or
    #: reviewed; `services/inventory.py` diffs the list and names what appeared.
    pattern: str = ""
    globs: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(x for x in (self.title, self.rule, self.why, self.how) if x)


@dataclass
class Job:
    """A long-running process started for an item: a training run, a data generation.

    The work of the projects ddflow is meant to take over is mostly WAITING -- a
    multi-hour training run, a 5M-record generation across an 8-replica model fleet --
    and a queue that knows only about files could not say whether the process an item
    depends on is still alive, finished, or died hours ago. What liveness means here is
    computed, never stored: see `services.jobs.status`.
    """

    id: str
    item: str = ""
    command: str = ""
    pid: int = 0
    host: str = ""
    #: The process's start time as the kernel reports it, so a REUSED pid is not
    #: mistaken for the job. "" where it cannot be read (not Linux).
    proc_start: str = ""
    log: str = ""
    cwd: str = ""
    started_at: str = ""
    by: str = ""
    ended_at: str = ""
    exit_code: int | None = None
    note: str = ""


@dataclass
class Memory:
    """One operational fact, true of THIS machine, repository or working state.

    Not a lesson (a transferable rule), not a journal entry (what happened), not a
    decision (how the software is built): "this box has 8 H200s", "use `-n 16`, never
    `-n auto`", "that reviewer can exit 0 having degenerated". Short by construction
    (`[memory] max_chars`), surfaced at every session start by `brief`, and never
    deleted -- a fact that stopped being true is FORGOTTEN with a reason, which is itself
    worth knowing the next time somebody believes it.

    The shape of the OptMem store the source projects kept beside their repository,
    moved into the log so it is shared by every worktree the moment it is written and
    travels with the code.
    """

    id: str
    text: str = ""
    tags: list[str] = field(default_factory=list)
    at: str = ""
    #: When it became true, if that is not when it was written down (an import).
    origin_at: str = ""
    by: str = ""
    source: str = ""
    #: Why it is no longer believed; "" while live.
    forgotten: str = ""

    @property
    def live(self) -> bool:
        return not self.forgotten


@dataclass
class Decision:
    """An architectural decision, in the log rather than in someone's memory.

    The shape is a deliberately small ADR: what was decided, why, and what it costs.
    The fields that make it *usable later* rather than merely recorded are:

    * ``globs`` — the code this decision governs. An agent about to write
      ``src/storage/*`` can be handed the decisions about storage without searching
      for them, which is the difference between a rule that is consulted and one that
      is merely filed.
    * ``alternatives`` — what was rejected. Without it, the next agent re-proposes the
      rejected option, and the only answer anyone remembers is "we discussed that".
    * ``superseded_by`` — decisions are never edited or deleted. A reversal is a NEW
      decision that names the old one, so the history of how the architecture got here
      survives, which is exactly what a rebuild needs.
    """

    id: str
    title: str = ""
    context: str = ""  # the forces: why a decision was needed at all
    decision: str = ""  # what was chosen
    consequences: str = ""  # what it costs, including what it makes harder
    alternatives: str = ""  # what was rejected, and why
    globs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: Where this decision came from: an ADR path, a URL, a commit sha. `Lesson` has
    #: `seen_in` and `ResearchNote` has `sources`; a decision had only the prose
    #: `context`, so the import's vanished-source check could cover items, lessons,
    #: research and notes -- and not decisions. Parsing a path back out of a sentence
    #: is the anti-pattern that check exists to replace, so the field is the fix.
    sources: list[str] = field(default_factory=list)
    status: str = "accepted"  # proposed | accepted | superseded
    decided_by: str = ""  # operator | agent | a name
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str = ""
    at: str = ""
    item: str = ""

    @property
    def live(self) -> bool:
        return self.status == "accepted" and not self.superseded_by

    def text(self) -> str:
        return "\n".join(
            x
            for x in (self.title, self.context, self.decision, self.consequences, self.alternatives)
            if x
        )


@dataclass
class ResearchNote:
    id: str
    question: str = ""
    claim: str = ""
    mechanism: str = ""
    falsifier: str = ""
    probe: str = ""
    probe_output: str = ""
    verdict: str = ""  # CONFIRMED | REFUTED | THEORETICAL
    sources: list[str] = field(default_factory=list)
    #: Free-form labels. `Lesson` and `Decision` have always had these; research did
    #: not, so "which notes came from an import" had no honest answer -- `sources` holds
    #: an arXiv id for a hand-written note and `docs/RESEARCH.md:79` for an imported
    #: one, and telling those apart by their SHAPE is a heuristic pretending to be a
    #: fact. One marker, spelled the same way on all three.
    tags: list[str] = field(default_factory=list)
    budget: str = ""
    at: str = ""
    item: str = ""


@dataclass
class Session:
    id: str
    agent: str = ""
    model: str = ""
    started_at: str = ""
    ended_at: str = ""
    prompts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class State:
    items: dict[str, Item] = field(default_factory=dict)
    bugs: dict[str, Bug] = field(default_factory=dict)
    lessons: dict[str, Lesson] = field(default_factory=dict)
    research: dict[str, ResearchNote] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    memories: dict[str, Memory] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    #: `repo:ID` -> what `ddflow external sync` last OBSERVED of an item in a sibling
    #: repository: {"state", "title", "repo", "at"}. Recorded in this log, so a
    #: dependency on another project is decided from a fact with a date on it, and the
    #: fold stays pure -- nothing here reads another repository.
    external: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    cadences: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: gate id -> how many times recording it fired the pipeline-order check, and how
    #: many times it was recorded at all. `enforce_order` has defaulted to "warn" since
    #: it was written and nothing measured whether that warning is routine or rare --
    #: so neither "make it block" nor "turn it off" could be argued, only asserted.
    #: A rate needs a numerator AND a denominator; both are counted here.
    gate_order: dict[str, dict[str, int]] = field(default_factory=dict)
    releases: list[Release] = field(default_factory=list)
    #: version -> the release request awaiting approval (gitflow + pull requests), as
    #: {"branch", "number", "url", "base", "forge"}. Tagging it removes it.
    pending_releases: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: knob -> the recorded workflow CHOICE: {"value", "by", "agent", "user", "at",
    #: "reason"}. `by` is "explicit" or "default" -- a default applied at first use is
    #: recorded so the project keeps following it even if ddflow's default changes.
    flow_choices: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: item id -> the add that currently DEFINES it, in the shape of an `Item.contested`
    #: entry. What a rival add is compared against, and what becomes the first side of
    #: the contest when one arrives.
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_lamport: int = 0
    event_count: int = 0
    #: kind -> count, for events a non-strict fold could not interpret. Counted rather
    #: than ignored so a caller can refuse to act on a partially-understood log.
    skipped_kinds: dict[str, int] = field(default_factory=dict)

    # -- convenience views ----------------------------------------------------------
    def phases(self) -> list[Item]:
        return [i for i in self.items.values() if i.kind == "phase" and not i.removed]

    def tasks(self, phase: str = "") -> list[Item]:
        """Tasks under ``phase``, INCLUDING sub-tasks nested any depth below it.

        A task may parent another task — that is what a sub-task is here, rather than a
        separate concept with its own rules. Everything that applies to a task applies
        to a sub-task unchanged: it can declare its own globs, carry its own
        dependencies, be claimed by a different agent, and run in parallel with its
        siblings when nothing links them.
        """
        live = [i for i in self.items.values() if i.kind == "task" and not i.removed]
        if not phase:
            return live
        wanted = self.descendants(phase)
        return [i for i in live if i.id in wanted]

    def _child_index(self) -> dict[str, list[Item]]:
        """`parent -> [children]`, built once per fold and cached on the State.

        `children()` was a full scan of `items`, and `descendants()` / `ancestors()` /
        `_is_umbrella()` call it once per node per candidate — so `plan()` was roughly
        quadratic in queue size. Measured before this (1 phase + N tasks):

            n=100  2.0 ms      n=400  18.8 ms      n=800  46.1 ms

        with cProfile attributing 74% of `plan()` at n=800 to 1,600 calls into
        `children`. Harmless at realistic sizes and a cheap fix, which is exactly the
        kind of thing that stays unfixed until someone has 2,000 items.

        Cached on the instance rather than memoised globally: a `State` is the result
        of one `fold` and is never mutated afterwards, so the index cannot go stale —
        and a global cache keyed on a mutable object would be a bug waiting for the
        first caller who does mutate one.
        """
        idx = getattr(self, "_children_cache", None)
        if idx is None:
            idx = {}
            for i in self.items.values():
                if not i.removed and i.parent:
                    idx.setdefault(i.parent, []).append(i)
            object.__setattr__(self, "_children_cache", idx)
        return idx

    def children(self, item_id: str) -> list[Item]:
        return list(self._child_index().get(item_id, ()))

    def descendants(self, item_id: str) -> set[str]:
        """Every item below ``item_id``, transitively.

        Iterative and cycle-guarded: a parent chain is operator-authored, so it can be
        both deep and — if someone makes a mistake — circular, and a health check that
        blows the stack while diagnosing a bad plan is no use.
        """
        seen: set[str] = set()
        stack = [item_id]
        while stack:
            node = stack.pop()
            for child in self.children(node):
                if child.id in seen:
                    continue
                seen.add(child.id)
                stack.append(child.id)
        return seen

    def ancestors(self, item_id: str) -> list[Item]:
        """The parent chain above ``item_id``, nearest first.

        Cycle-guarded for the same reason ``descendants`` is: the chain is
        operator-authored, so a mistake can make it circular, and the code that walks
        it is the code that diagnoses bad plans.
        """
        out: list[Item] = []
        seen = {item_id}
        node = self.items.get(item_id)
        while node is not None and node.parent and node.parent not in seen:
            seen.add(node.parent)
            node = self.items.get(node.parent)
            if node is None or node.removed:
                break
            out.append(node)
        return out

    def open_descendants(self, item_id: str) -> list[Item]:
        """Descendants that are neither done nor abandoned — what blocks completion."""
        return [
            self.items[i]
            for i in sorted(self.descendants(item_id))
            if self.items[i].state not in (DONE, ABANDONED)
        ]

    def active_leases(self, now: float, grace_s: int = 0) -> dict[str, Lease]:
        return {
            i.id: i.lease
            for i in self.items.values()
            if i.lease and not i.lease.expired(now, grace_s)
        }

    def expired_leases(self, now: float, grace_s: int = 0) -> dict[str, Lease]:
        return {
            i.id: i.lease for i in self.items.values() if i.lease and i.lease.expired(now, grace_s)
        }


def _item(state: State, ev: Event, kind: str) -> Item:
    it = state.items.get(ev.subject)
    if it is None:
        it = Item(id=ev.subject, kind=kind, created_at=ev.ts)
        state.items[ev.subject] = it
    return it


# ---------------------------------------------------------------------------------
# Event handlers.
#
# One function per event kind, and ``HANDLERS`` is the ONLY declaration of the
# vocabulary -- ``events.KINDS`` is derived from it. The alternative, a long if/elif
# ladder beside a separately-maintained set of kind strings, makes the vocabulary and
# its interpretation two lists that nothing forces to agree: a kind can be declared and
# never handled (folds to "nothing happened") or handled and never declared (rejected
# at append time). Deriving one from the other removes the disagreement structurally.
# ---------------------------------------------------------------------------------


def _safe_parent(st: State, item_id: str, parent: str) -> str:
    """``parent``, unless accepting it would make ``item_id`` its own ancestor.

    A self-parented item is its own open descendant, so it is permanently an umbrella:
    never offered, never claimable, never completable, and the only diagnosis is a
    blocked item that names itself. No CLI or MCP path can produce one today, but
    `fold` must survive a hand-written or future-version event — it is the one function
    in this package that is fed arbitrary JSON from disk and has no right to refuse it.
    """
    if not parent or parent == item_id:
        return ""
    seen = {item_id, parent}
    node = st.items.get(parent)
    while node is not None and node.parent:
        if node.parent == item_id:
            return ""
        if node.parent in seen:
            break
        seen.add(node.parent)
        node = st.items.get(node.parent)
    return parent


def _definition(ev: Event) -> dict[str, Any]:
    return {
        "event": ev.id or ev.compute_id(),
        "agent": ev.agent,
        "lamport": ev.lamport,
        "ts": ev.ts,
        "title": ev.data.get("title", ""),
        "body": ev.data.get("body", ""),
        "data": dict(ev.data),
    }


def _h_added(st: State, ev: Event, kind: str) -> None:
    """Define an item -- or, when a live item is already defined differently, contest it.

    A re-add of a REMOVED id is a new definition and ends any old contest. The same
    definition arriving twice (identical data) is not a contest: nothing would be lost.
    """
    prior = st.definitions.get(ev.subject)
    it = _item(st, ev, kind)
    mine = _definition(ev)
    if prior is not None and not it.removed and prior["data"] != mine["data"]:
        if not it.contested:
            it.contested = [prior]
        if all(d["data"] != mine["data"] for d in it.contested):
            it.contested.append(mine)
    elif it.removed:
        it.contested = []
    st.definitions[ev.subject] = mine
    _apply_definition(st, it, ev.data, kind)


def _apply_definition(st: State, it: Item, d: dict[str, Any], kind: str) -> None:
    it.kind = kind
    it.title = d.get("title", it.title)
    it.parent = _safe_parent(st, it.id, d.get("parent", it.parent))
    it.needs = list(d.get("needs", it.needs))
    it.globs = list(d.get("globs", it.globs))
    it.resources = list(d.get("resources", it.resources))
    it.body = d.get("body", it.body)
    it.tags = list(d.get("tags", it.tags))
    it.priority = int(d.get("priority", it.priority))
    it.source = d.get("source", it.source)
    for f in ("line", "port_of", "port_from", "port_strategy", "promote_from", "promote_to"):
        setattr(it, f, d.get(f, getattr(it, f)))
    it.removed = False


def _h_updated(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    for f in ("title", "parent", "body", "blocked_reason", "line"):
        if f in d:
            setattr(it, f, _safe_parent(st, it.id, d[f]) if f == "parent" else d[f])
    for f in ("needs", "globs", "tags", "resources"):
        if f in d:
            setattr(it, f, list(d[f]))
    if "priority" in d:
        it.priority = int(d["priority"])


def _h_removed(st: State, ev: Event, kind: str) -> None:
    _item(st, ev, kind).removed = True


def _claim(lease: Lease) -> dict[str, Any]:
    return {"holder": lease.holder, "event": lease.event, "lease": asdict(lease)}


def _span(lease: dict[str, Any]) -> tuple[float, float]:
    """A claim's interval by its own evidence: acquired, to its last renewal plus TTL."""
    return lease["acquired_at"], lease["renewed_at"] + lease["ttl_s"]


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two claims' intervals intersect -- on BOTH ends. One-sided, a claim that ended
    before the other began read as its rival whenever it happened to fold second."""
    (a0, a1), (b0, b1) = _span(a), _span(b)
    return a0 <= b1 and b0 <= a1


def _clashing(claims: list[dict[str, Any]], claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The claims among ``claims`` that overlapped ``claim``: another holder's, by both
    claims' own windows."""
    return [
        h
        for h in claims
        if h["holder"] != claim["holder"]
        and h["event"] != claim["event"]
        and _overlaps(h["lease"], claim["lease"])
    ]


def _join(it: Item, claims: list[dict[str, Any]]) -> None:
    """Add claims to the lease contest, once each. Keyed by the ACQUIRING EVENT, never by
    holder name: one holder can hold an item twice (claimed, lost, claimed again), and
    those are two claims that can each be contested. A claim already there keeps the
    later evidence -- a renewal since it joined widens the window everything pairwise
    reads."""
    have = {h["event"]: h for h in it.lease_contest}
    for c in claims:
        old = have.get(c["event"])
        if old is None:
            it.lease_contest.append(c)
            have[c["event"]] = c
            continue
        old["lease"]["renewed_at"] = max(old["lease"]["renewed_at"], c["lease"]["renewed_at"])
        if "overlapped_by" in c:
            old.setdefault("overlapped_by", c["overlapped_by"])


def _displace(it: Item, lost: dict[str, Any], by: dict[str, Any]) -> None:
    it.displaced = [*it.displaced, {**lost, "by": by}][-MAX_DISPLACED:]


def _hold(it: Item, lease: Lease) -> None:
    """Make ``lease`` the displayed one, and point the item at its tree."""
    it.lease = lease
    it.worktree = lease.worktree or it.worktree
    it.branch = lease.branch or it.branch


def _h_lease_acquired(st: State, ev: Event) -> None:
    """A claim, weighed against every claim it could have collided with.

    `acquire` never grants a claim over another holder's live one in one log -- a live
    lease is refused even with --force, and a takeover needs a release, a recorded
    expiry, or the lease past its TTL (and grace) at the moment of the claim. So two
    claims whose intervals intersect can only come from clones that did not see each
    other: a contest. The comparison is on the claims' own times, never on "now" (the
    fold is pure), and against EVERY claim the item still knows -- the displayed lease,
    each one already contested, each one a takeover displaced -- so the contest is the
    claims that overlapped another, whatever order they fold in. It is never narrowed to
    the claims that met the displayed one: that hid real double claims. Who met whom is
    read pairwise from it (`Item.lease_clashes`).

    Which claim is DISPLAYED is decided by wall time, not fold order: the later
    acquisition. A claim that ended before the displayed one began is history arriving
    late; it is remembered as displaced (a late renewal may yet prove it overlapped) and
    never replaces the lease.
    """
    d = ev.data
    it = _item(st, ev, d.get("kind", "task"))
    new = Lease(
        holder=d.get("holder", ev.agent),
        acquired_at=float(d.get("at", 0.0)),
        renewed_at=float(d.get("at", 0.0)),
        ttl_s=int(d.get("ttl_s", 1800)),
        worktree=d.get("worktree", ""),
        branch=d.get("branch", ""),
        globs=list(d.get("globs", [])),
        note=d.get("note", ""),
        resources=list(d.get("resources", [])),
        event=ev.id or ev.compute_id(),
    )
    mine = _claim(new)
    cur = it.lease
    live = cur is not None and cur.holder != new.holder and not cur.expired_at
    known = [
        *it.lease_contest,
        *({k: v for k, v in e.items() if k != "by"} for e in it.displaced),
        *([_claim(cur)] if live else []),
    ]
    rivals = _clashing(known, mine)
    if rivals:
        _join(it, [*rivals, mine])
    if not live:
        _hold(it, new)
        return
    held = _claim(cur)
    if _overlaps(held["lease"], mine["lease"]):
        if new.acquired_at >= cur.acquired_at:
            _hold(it, new)
        return
    if new.acquired_at < cur.acquired_at:
        _displace(it, mine, held)  # late history: `cur` took over from it
        return
    _displace(it, held, mine)
    _hold(it, new)


#: How many TTL-displaced claims an item remembers, oldest dropped first. A cap rather
#: than a lapse rule, because "this claim can no longer be contradicted" is exactly what
#: the fold cannot know: a clone may deliver a renewal from any point in the past. Each
#: entry needs a takeover without a release -- `--force` or a non-`report` reclaim
#: policy, per takeover, on one item -- so a real chain is one or two long, and eight is
#: far past anything but a pathological loop, which `loops` reports on its own.
MAX_DISPLACED = 8


def _late_renewal(it: Item, d: dict[str, Any]) -> None:
    """A renewal of a claim a TTL takeover displaced, folded after the takeover.

    One log cannot produce it -- `renew` refuses anyone but the current holder -- so it
    came from a clone that never saw the takeover. It belongs to the holder's LATEST
    claim acquired at or before the renewal's own time: a holder's claims follow one
    another in its own clone, so that is the one it was renewing. If the renewal kept
    that claim live up to the moment the claim that displaced it was made (or was made
    after it), the takeover was of a LIVE claim: a contest naming the displaced claim,
    the one that displaced it, and the displayed one if that is another again. A renewal
    that still left the claim lapsed by then changes nothing.
    """
    if "at" not in d:
        return
    at = float(d["at"])
    own = [
        e for e in it.displaced if e["holder"] == d["holder"] and e["lease"]["acquired_at"] <= at
    ]
    if not own:
        return
    hit = max(own, key=lambda e: e["lease"]["acquired_at"])
    if at + hit["lease"]["ttl_s"] < hit["by"]["lease"]["acquired_at"]:
        return
    it.displaced = [e for e in it.displaced if e["event"] != hit["event"]]
    claim = {k: v for k, v in hit.items() if k != "by"}
    claim["lease"]["renewed_at"] = max(claim["lease"]["renewed_at"], at)
    claim["overlapped_by"] = hit["by"]["holder"]
    _join(it, [claim, hit["by"], *([_claim(it.lease)] if it.lease is not None else [])])


def _h_lease_renewed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if not it:
        return
    d = ev.data
    # Only the CURRENT claim may be renewed. Lamport values are computed independently on
    # unsynced clones, so merging two shards can order a former holder's renewal after
    # a later re-acquisition by someone else -- and applying it would point the live
    # lease at the dead agent's worktree, which every destructive path then targets. A
    # renewal stamped before the displayed claim was acquired is not of it either, even
    # from the same holder: it renewed an EARLIER claim of theirs.
    stale = it.lease is not None and "at" in d and float(d["at"]) < it.lease.acquired_at
    if "holder" in d and (it.lease is None or d["holder"] != it.lease.holder or stale):
        _late_renewal(it, d)
        return
    if not it.lease:
        return
    # A renewal may only move the clock FORWARD. A stale renewal reordered after a
    # re-acquisition would otherwise set `renewed_at` back to its own older timestamp,
    # and a live lease would read as expired -- inviting another agent to take an item
    # someone is actively editing.
    at = float(d.get("at", it.lease.renewed_at))
    if at < it.lease.renewed_at:
        return
    it.lease.renewed_at = at
    # Absent keys leave the field alone; only a present key updates, so a plain
    # heartbeat never clears an attachment.
    if "worktree" in d:
        it.lease.worktree = d["worktree"]
        it.worktree = d["worktree"] or it.worktree
    if "branch" in d:
        it.lease.branch = d["branch"]
        it.branch = d["branch"] or it.branch
    if "globs" in d:
        it.lease.globs = list(d["globs"])
    if "resources" in d:
        it.lease.resources = list(d["resources"])


def _released_claim(claims: list[dict[str, Any]], d: dict[str, Any]) -> str:
    """The acquiring event a release names, among ``claims``; "" when none.

    By `event` when the release carries it (every release written since B191 does). An
    older release names only a holder, and then it is taken to end that holder's LATEST
    claim -- the one it held when it released.
    """
    if d.get("event"):
        return d["event"] if any(c["event"] == d["event"] for c in claims) else ""
    own = [c for c in claims if c["holder"] == d.get("holder")]
    return max(own, key=lambda c: c["lease"]["acquired_at"])["event"] if own else ""


def _h_lease_gone(st: State, ev: Event) -> None:
    """Handle `lease.released` and `lease.expired`.

    Two rules, both learned from a cross-family review that probed reordered shards:

    1. **A release only ends the lease it names.** Shard merges can order a former
       holder's release AFTER a newer acquisition, and unconditionally clearing the
       lease then destroys the CURRENT holder's claim — another agent can take the item
       while the first is mid-edit. This is the same class as the stale-renewal bug
       fixed earlier; that fix patched one handler and left its siblings, which is
       exactly the incomplete-fix failure the rule against it describes.
    2. **Expiry keeps the lease object, marked expired.** Deleting it loses the
       worktree pointer, so `State.expired_leases()` could never report an expiry and a
       cleanup pass sees an item with no lease at all. Keeping it with `ttl_s = 0` makes
       `expired()` true, so it leaves `active_leases` and appears in `expired_leases`
       with its worktree intact — which is what recovery needs to protect the tree.
    """
    it = st.items.get(ev.subject)
    if not it:
        return
    d = ev.data
    holder = d.get("holder")
    if ev.kind == "lease.released" and holder is not None:
        # A claim given up can no longer contradict a takeover of it.
        gone = _released_claim(it.displaced, d)
        it.displaced = [e for e in it.displaced if e["event"] != gone]
        gone = _released_claim(it.lease_contest, d)
        if gone:
            _withdraw_claim(it, gone)
            return
    elif ev.kind == "lease.expired" and holder is not None:
        # A contestant whose expiry is RECORDED is no longer live, so it cannot collide
        # with a claim that comes after -- its snapshot has to say so.
        gone = _released_claim(it.lease_contest, d)
        for h in it.lease_contest:
            if h["event"] == gone:
                h["lease"].update(ttl_s=0, expired_at=ev.ts)
    if not it.lease:
        return
    if holder is not None and holder != it.lease.holder:
        return
    if d.get("event") and d["event"] != it.lease.event:
        return
    if ev.kind == "lease.expired":
        it.lease.ttl_s = 0
        it.lease.expired_at = ev.ts
        return
    it.lease = None
    _redisplay(it)


def _redisplay(it: Item) -> None:
    """The displayed lease was released while a contest stands: the latest contestant is
    displayed -- the fold's own rule, and what the log without the released claim shows.
    A displaced claim is never promoted: it lapsed before a takeover, and reviving it
    would turn the next ordinary claim into a recovery."""
    if it.lease is None and it.lease_contest:
        _hold(it, Lease(**max(it.lease_contest, key=lambda h: h["lease"]["acquired_at"])["lease"]))


def _withdraw_claim(it: Item, event: str) -> None:
    """A contested claim was released: it is withdrawn -- it, and nothing else. A claim
    left with no overlap partner has nothing to resolve: it leaves the contest (all of
    them do, once no two overlapped) and goes back on the record as history the
    displayed lease displaced, where a later claim is still weighed against it."""
    it.lease_contest = [h for h in it.lease_contest if h["event"] != event]
    if it.lease is not None and it.lease.event == event:
        it.lease = None
    _redisplay(it)
    alone = [h for h in it.lease_contest if not _clashing(it.lease_contest, h)]
    if not alone or it.lease is None:
        return
    held, kept = _claim(it.lease), {e["event"] for e in it.displaced}
    for h in alone:
        if h["event"] not in (held["event"], *kept):
            _displace(it, h, held)
    it.lease_contest = [h for h in it.lease_contest if h not in alone]


def _h_resolved(st: State, ev: Event) -> None:
    """An operator settled a contest: the kept definition and/or claim is re-applied.

    The payload CARRIES what was kept rather than pointing at it, so the fold needs no
    second pass over the log, and the record says what was decided in its own words.
    """
    it = st.items.get(ev.subject)
    if it is None:
        return
    d = ev.data
    keep = d.get("definition")
    if keep:
        _apply_definition(st, it, keep.get("data", {}), d.get("kind", it.kind))
        st.definitions[it.id] = keep
        it.contested = []
    claim = d.get("claim")
    if claim:
        lease = Lease(**claim["lease"])
        # The operator's decision is itself a sign of life: the kept holder's TTL runs
        # from here, not from a claim that may be hours old.
        lease.renewed_at = max(lease.renewed_at, float(d.get("at", 0.0)))
        # What the resolution did not release stays on the record: claims that still
        # overlap each other are still a contest -- this one settled the kept claim's,
        # not theirs -- and a claim with no partner left is history the kept claim
        # displaced, which a later claim is still weighed against.
        rest = [h for h in it.lease_contest if h["event"] != claim["event"]]
        standing = [h for h in rest if _clashing(rest, h)]
        on_record = {claim["event"], *(h["event"] for h in standing)}
        on_record |= {e["event"] for e in it.displaced}
        for h in [*rest, *([_claim(it.lease)] if it.lease is not None else [])]:
            if h["event"] not in on_record:
                _displace(it, h, claim)
                on_record.add(h["event"])
        it.displaced = [e for e in it.displaced if e["event"] != claim["event"]]
        _hold(it, lease)
        it.lease_contest = standing


def _h_state(new_state: str):
    def handler(st: State, ev: Event) -> None:
        it = _item(st, ev, ev.data.get("kind", "task"))
        it.state = new_state
        if new_state == RUNNING:
            it.blocked_reason = ""
        elif new_state == BLOCKED:
            it.blocked_reason = ev.data.get("reason", "")
        elif new_state == DONE:
            it.completed_at = ev.ts
            # `or`, not a default: `complete` without --sha writes "sha": "", which used
            # to ERASE the sha `merge` recorded -- so no finished item was ever found on
            # any branch, and every version's item list and item-based bump were empty.
            it.merged_sha = ev.data.get("sha") or it.merged_sha
            # The importer has always written this -- `{"imported": True, "evidence":
            # "ticked in docs/todo.md:41"}` -- and the fold has always thrown it away,
            # so the one record of WHY an item was closed without running a single gate
            # existed only in the raw log. Third instance of this class in this series.
            it.completion_evidence = ev.data.get("evidence", it.completion_evidence)
        elif new_state == ABANDONED:
            it.blocked_reason = ev.data.get("reason", "")

    return handler


def _h_unblocked(st: State, ev: Event) -> None:
    """Release a BLOCKED item back to OPEN. Anything else is left exactly as it is.

    Without this there was no way back: a blocked item could only be forced past with
    `claim --force`, which also overrides dependencies and live leases. An import that
    lands a project's deferred work as BLOCKED needs the one-word inverse, or "held" is
    a synonym for "lost".
    """
    it = st.items.get(ev.subject)
    if it is None or it.state != BLOCKED:
        return
    it.state = OPEN
    it.blocked_reason = ""


def _count_recording(st: State, gate: str) -> None:
    st.gate_order.setdefault(gate, {"fired": 0, "recorded": 0})["recorded"] += 1


def _h_gate(outcome: str):
    def handler(st: State, ev: Event) -> None:
        d = ev.data
        it = _item(st, ev, d.get("kind", "task"))
        gate = d.get("gate", "")
        if outcome == "started":
            it.gates.setdefault(gate, GateRecord(gate=gate))
            it.gates[gate].at = ev.ts
        else:
            # The denominator for the order-violation rate. `started` is excluded: it
            # is not a recording, and counting it would deflate the rate by the number
            # of command gates, which are the ones that emit it.
            _count_recording(st, gate)
            it.gates[gate] = GateRecord(
                gate=gate,
                outcome=outcome,
                at=ev.ts,
                by=d.get("by", ev.agent),
                reason=d.get("reason", ""),
                evidence=dict(d.get("evidence", {})),
            )

    return handler


def _h_worktree_created(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.worktree = ev.data.get("path", "")
    it.branch = ev.data.get("branch", "")
    it.base = ev.data.get("base", "")


def _h_worktree_adopted(st: State, ev: Event) -> None:
    _h_worktree_created(st, ev)
    _item(st, ev, ev.data.get("kind", "task")).adopted = True


def _h_worktree_merged(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.merged_sha = ev.data.get("sha", "")
    it.landed_before = ev.data.get("landed_before", it.landed_before)
    it.landed_after = ev.data.get("landed_after", it.landed_after)


def _h_port_applied(st: State, ev: Event) -> None:
    it = _item(st, ev, ev.data.get("kind", "task"))
    it.port = dict(ev.data)


def _h_flow_chosen(st: State, ev: Event) -> None:
    d = ev.data
    st.flow_choices[d.get("knob", ev.subject)] = {
        "value": d.get("value"),
        "by": d.get("by", "explicit"),
        "agent": ev.agent,
        "user": d.get("user", ""),
        "at": ev.ts,
        "reason": d.get("reason", ""),
    }


def _h_worktree_removed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if it:
        it.worktree = ""


_PR_FIELDS = (
    "number",
    "url",
    "forge",
    "base",
    "head",
    "state",
    "review",
    "checks",
    "head_sha",
    "merge_sha",
    "feedback",
    "author_model",
)


def _pr(st: State, ev: Event) -> tuple[Item, PullRequest]:
    it = _item(st, ev, ev.data.get("kind", "task"))
    if it.pr is None:
        it.pr = PullRequest()
    for key in _PR_FIELDS:
        if key in ev.data:
            setattr(it.pr, key, ev.data[key])
    it.pr.synced_at = ev.ts
    return it, it.pr


def _h_pr_opened(st: State, ev: Event) -> None:
    """Opened OR re-pushed: either way the work is now the reviewers' to judge.

    Feedback from an earlier round is cleared, because it was answered by this push --
    carrying it forward would hand the next agent a list of things already fixed.
    """
    it, pr = _pr(st, ev)
    pr.state = "open"
    pr.review = ev.data.get("review", "pending")
    pr.feedback = ""
    it.state = REVIEW
    it.blocked_reason = ""


def _h_pr_synced(st: State, ev: Event) -> None:
    _pr(st, ev)


def _h_pr_changes_requested(st: State, ev: Event) -> None:
    """Back to the queue, WITH the review attached -- or parked, if the operator said so.

    OPEN rather than RUNNING: nobody holds it, and RUNNING-without-a-lease is the shape
    of a crash. The branch and worktree stay on the item, so the next claim adopts the
    same tree and a push updates the same request.
    """
    it, pr = _pr(st, ev)
    pr.review = "changes_requested"
    pr.requested_head = pr.head_sha
    pr.rounds += 1
    if ev.data.get("block"):
        it.state = BLOCKED
        it.blocked_reason = f"changes requested on {pr.url or 'its pull request'}"
    else:
        it.state = OPEN


def _h_pr_merged(st: State, ev: Event) -> None:
    it, pr = _pr(st, ev)
    pr.state = "merged"
    it.merged_sha = pr.merge_sha or it.merged_sha


def _h_pr_closed(st: State, ev: Event) -> None:
    """Closed without merging is a reviewer's NO, and a no is for a person to read.

    Parked, never silently reopened: re-offering it would send an agent to redo work a
    human just declined, which is a loop with a person in it.
    """
    it, pr = _pr(st, ev)
    pr.state = "closed"
    it.state = BLOCKED
    it.blocked_reason = f"pull request closed without merging: {pr.url}"


def _h_release_opened(st: State, ev: Event) -> None:
    st.pending_releases[ev.subject] = {
        k: ev.data.get(k, "") for k in ("branch", "number", "url", "base", "forge")
    }


def _h_release_closed(st: State, ev: Event) -> None:
    st.pending_releases.pop(ev.subject, None)


def _h_release_tagged(st: State, ev: Event) -> None:
    d = ev.data
    st.pending_releases.pop(d.get("version", ev.subject), None)
    st.releases.append(
        Release(
            version=d.get("version", ev.subject),
            tag=d.get("tag", ""),
            sha=d.get("sha", ""),
            branch=d.get("branch", ""),
            items=list(d.get("items", [])),
            at=ev.ts,
            pushed=bool(d.get("pushed")),
            line=d.get("line", ""),
        )
    )


def _h_bug_found(st: State, ev: Event) -> None:
    """Merge, never replace. Folding `bug.found` after `bug.fixed` used to clear
    `fixed_at`, so a bug that was fixed (with its regression test) read as open."""
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.item = ev.data.get("item", "") or bug.item
    bug.summary = ev.data.get("summary", "") or bug.summary
    bug.found_at = bug.found_at or ev.ts


def _h_bug_fixed(st: State, ev: Event) -> None:
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.fixed_at = ev.ts
    bug.regression_test = ev.data.get("regression_test", "")
    bug.lesson = ev.data.get("lesson", "")


def _h_lesson(st: State, ev: Event) -> None:
    """Merge, never replace — the same rule `_h_decision` and `_h_bug_found` follow.

    Shard merges reorder, so a re-record of a lesson can fold AFTER the supersession
    that retired it. Rebuilding the object wholesale dropped `superseded_by`, and the
    retired lesson walked back into the reconstruction brief's standing knowledge and
    into `recall` — advice the project had explicitly replaced, presented as current.
    """
    d = ev.data
    prev = st.lessons.get(ev.subject)
    st.lessons[ev.subject] = Lesson(
        id=ev.subject,
        title=d.get("title", "") or (prev.title if prev else ""),
        rule=d.get("rule", "") or (prev.rule if prev else ""),
        why=d.get("why", "") or (prev.why if prev else ""),
        how=d.get("how", "") or (prev.how if prev else ""),
        summary=d.get("summary", "") or (prev.summary if prev else ""),
        seen_in=list(d.get("seen_in", [])),
        tags=list(d.get("tags", prev.tags if prev else [])),
        # Merged like every other field, not replaced: a re-record that omits the pattern
        # must not silently disarm the ratchet. Re-scanning and finding nothing is how an
        # inventory shrinks; forgetting the pattern is how it disappears.
        pattern=d.get("pattern", "") or (prev.pattern if prev else ""),
        globs=list(d.get("globs", prev.globs if prev else [])),
        sites=list(d.get("sites", prev.sites if prev else [])),
        at=prev.at if prev and prev.at else ev.ts,
        superseded_by=prev.superseded_by if prev else "",
    )
    for sid in d.get("supersedes", []):
        if sid in st.lessons:
            st.lessons[sid].superseded_by = ev.subject
    if prev and prev.seen_in:
        st.lessons[ev.subject].seen_in = list(
            dict.fromkeys(prev.seen_in + st.lessons[ev.subject].seen_in)
        )


def _h_decision(st: State, ev: Event) -> None:
    """Record an architectural decision.

    Merges rather than replaces, for the same reason every other handler here does: a
    shard merge can deliver a supersession before the decision it supersedes, and
    replacing would drop the marker that is already correct.
    """
    d = ev.data
    prev = st.decisions.get(ev.subject)
    dec = Decision(
        id=ev.subject,
        title=d.get("title", "") or (prev.title if prev else ""),
        context=d.get("context", "") or (prev.context if prev else ""),
        decision=d.get("decision", "") or (prev.decision if prev else ""),
        consequences=d.get("consequences", "") or (prev.consequences if prev else ""),
        alternatives=d.get("alternatives", "") or (prev.alternatives if prev else ""),
        globs=list(d.get("globs", prev.globs if prev else [])),
        tags=list(d.get("tags", prev.tags if prev else [])),
        sources=list(d.get("sources", prev.sources if prev else [])),
        status=d.get("status", "accepted"),
        decided_by=d.get("decided_by", "") or (prev.decided_by if prev else ""),
        supersedes=list(d.get("supersedes", prev.supersedes if prev else [])),
        superseded_by=prev.superseded_by if prev else "",
        at=prev.at if prev and prev.at else ev.ts,
        item=d.get("item", "") or (prev.item if prev else ""),
    )
    # `superseded_by` and `status` are one fact, so derive the second from the first
    # instead of storing it twice and hoping they agree. A shard merge can deliver
    # `decision.superseded` BEFORE a re-record of the decision it retired; the
    # re-record then carried the default `status="accepted"` and the reconstruction
    # presented a reversed decision as the one in force.
    if dec.superseded_by:
        dec.status = "superseded"
    st.decisions[ev.subject] = dec
    for old in dec.supersedes:
        target = st.decisions.setdefault(old, Decision(id=old))
        target.superseded_by = ev.subject
        target.status = "superseded"


def _h_decision_superseded(st: State, ev: Event) -> None:
    """Mark a decision replaced. Never deletes: the history of how the architecture
    got here is the part a rebuild most needs."""
    target = st.decisions.setdefault(ev.subject, Decision(id=ev.subject))
    target.superseded_by = ev.data.get("by", "")
    target.status = "superseded"


def _h_research(st: State, ev: Event) -> None:
    d = ev.data
    st.research[ev.subject] = ResearchNote(
        id=ev.subject,
        question=d.get("question", ""),
        claim=d.get("claim", ""),
        mechanism=d.get("mechanism", ""),
        falsifier=d.get("falsifier", ""),
        probe=d.get("probe", ""),
        probe_output=d.get("probe_output", ""),
        verdict=d.get("verdict", "THEORETICAL"),
        sources=list(d.get("sources", [])),
        tags=list(d.get("tags", [])),
        budget=d.get("budget", ""),
        at=ev.ts,
        item=d.get("item", ""),
    )


def _h_job_started(st: State, ev: Event) -> None:
    d = ev.data
    st.jobs[ev.subject] = Job(
        id=ev.subject,
        item=d.get("item", ""),
        command=d.get("command", ""),
        pid=int(d.get("pid", 0) or 0),
        host=d.get("host", ""),
        proc_start=str(d.get("proc_start", "")),
        log=d.get("log", ""),
        cwd=d.get("cwd", ""),
        started_at=ev.ts,
        by=ev.agent,
    )


def _h_job_ended(st: State, ev: Event) -> None:
    j = st.jobs.get(ev.subject)
    if j is None:
        return
    j.ended_at = ev.ts
    code = ev.data.get("exit_code")
    j.exit_code = int(code) if code is not None else None
    j.note = ev.data.get("note", "") or j.note


def _h_external(st: State, ev: Event) -> None:
    st.external[ev.subject] = {
        "state": ev.data.get("state", ""),
        "title": ev.data.get("title", ""),
        "repo": ev.data.get("repo", ""),
        "at": ev.ts,
    }


def _h_memory(st: State, ev: Event) -> None:
    """Merge, never replace -- a re-record that omits a field keeps the old one, and a
    re-record of a forgotten memory brings it back, which is what re-recording it means."""
    d = ev.data
    prev = st.memories.get(ev.subject)
    st.memories[ev.subject] = Memory(
        id=ev.subject,
        text=d.get("text", "") or (prev.text if prev else ""),
        tags=list(d.get("tags", prev.tags if prev else [])),
        at=prev.at if prev and prev.at else ev.ts,
        origin_at=d.get("origin_at", "") or (prev.origin_at if prev else ""),
        by=prev.by if prev and prev.by else ev.agent,
        source=d.get("source", "") or (prev.source if prev else ""),
        forgotten="",
    )


def _h_memory_forgotten(st: State, ev: Event) -> None:
    m = st.memories.get(ev.subject)
    if m is not None:
        m.forgotten = ev.data.get("reason", "") or "forgotten"


def _session(st: State, ev: Event) -> Session:
    return st.sessions.setdefault(ev.subject, Session(id=ev.subject, agent=ev.agent))


def _h_session_started(st: State, ev: Event) -> None:
    """Merge, never replace: a reordered shard can deliver a prompt before its
    session.started, and replacing the object would drop an event that IS in the log."""
    s = _session(st, ev)
    s.model = ev.data.get("model", "") or s.model
    s.started_at = s.started_at or ev.ts


def _h_session_prompt(st: State, ev: Event) -> None:
    s = _session(st, ev)
    s.prompts.append(
        {
            "at": ev.ts,
            "text": ev.data.get("text", ""),
            "item": ev.data.get("item", ""),
            "seq": len(s.prompts),
        }
    )


def _h_session_note(st: State, ev: Event) -> None:
    """A note, with the fields that make it addressable afterwards.

    `seq`, `ident` and `source` used to be dropped here, which is how a projection
    quietly decides a field does not exist: the importer wrote `ident` on every note to
    make a second import idempotent, the fold discarded it, and the check that read it
    back compared `""` against `""` and reported "already imported" for nothing.
    """
    note = {
        "at": ev.ts,
        "text": ev.data.get("text", ""),
        "item": ev.data.get("item", ""),
    }
    # `seq` is assigned HERE, positionally, exactly as `_h_session_prompt` does -- the
    # caller's number is ignored. `apply_import` numbers from a fresh `enumerate` on
    # every run and writes into two fixed session ids, and `_h_session_started` merges
    # rather than replaces, so an incremental re-import appended notes 0,1,2 beside the
    # first run's 0,1,2. The index keys on `(session, 10_000 + seq)`, so each new note
    # silently overwrote an earlier one -- and `prompts_fts` then held two rows under
    # one doc id, so a query matching the OLD text resolved to the surviving row and
    # returned text not containing the query terms. One authority for the number.
    sess = _session(st, ev)
    note["seq"] = len(sess.notes)
    for key in ("ident", "source"):
        if ev.data.get(key) not in (None, ""):
            note[key] = ev.data[key]
    # When the note records something that happened BEFORE it was written down -- an
    # imported journal entry, a memory dated months ago -- keep both: `at` is when
    # ddflow learned it, `origin_at` is when it was true.
    if ev.data.get("at"):
        note["origin_at"] = ev.data["at"]
    sess.notes.append(note)


def _h_session_ended(st: State, ev: Event) -> None:
    _session(st, ev).ended_at = ev.ts


def _h_gate_out_of_order(st: State, ev: Event) -> None:
    """One recording that reached a gate before its predecessors had run.

    Counted rather than merely printed, because `enforce_order`'s default is a
    judgement nobody had evidence for. The denominator lives on the same record: a
    count of violations without a count of recordings is a number that can be made to
    say anything.
    """
    row = st.gate_order.setdefault(ev.data.get("gate", ev.subject), {"fired": 0, "recorded": 0})
    row["fired"] += 1


def _h_cadence(st: State, ev: Event) -> None:
    st.cadences.setdefault(ev.subject, []).append(
        {
            "at": ev.ts,
            "by": ev.agent,
            "result": ev.data.get("result", ""),
            "evidence": ev.data.get("evidence", {}),
        }
    )


#: kind -> handler. The single declaration of the event vocabulary.
HANDLERS: dict[str, Callable[[State, Event], None]] = {
    "phase.added": lambda st, ev: _h_added(st, ev, "phase"),
    "task.added": lambda st, ev: _h_added(st, ev, "task"),
    "phase.updated": lambda st, ev: _h_updated(st, ev, "phase"),
    "task.updated": lambda st, ev: _h_updated(st, ev, "task"),
    "phase.removed": lambda st, ev: _h_removed(st, ev, "phase"),
    "task.removed": lambda st, ev: _h_removed(st, ev, "task"),
    "lease.acquired": _h_lease_acquired,
    "lease.renewed": _h_lease_renewed,
    "lease.released": _h_lease_gone,
    "lease.expired": _h_lease_gone,
    "item.started": _h_state(RUNNING),
    "item.blocked": _h_state(BLOCKED),
    "item.unblocked": _h_unblocked,
    "item.resolved": _h_resolved,
    "item.completed": _h_state(DONE),
    "item.abandoned": _h_state(ABANDONED),
    **{f"gate.{o}": _h_gate(o) for o in ("started", *GATE_OUTCOMES)},
    "worktree.created": _h_worktree_created,
    # NOT the same handler. The two kinds were folded identically on the reasoning that
    # "the item is bound to a path and a branch either way" -- which threw away the one
    # bit that matters: whether ddflow made the tree. `remove_on_merge` then deleted the
    # agent's own worktree, the failure the adoption commit called worse than the one it
    # was fixing and claimed to prevent. A distinction kept only in the LOG is a
    # distinction the projection cannot act on.
    "worktree.adopted": _h_worktree_adopted,
    "worktree.merged": _h_worktree_merged,
    "worktree.removed": _h_worktree_removed,
    "pr.opened": _h_pr_opened,
    "pr.synced": _h_pr_synced,
    "pr.changes_requested": _h_pr_changes_requested,
    "pr.merged": _h_pr_merged,
    "pr.closed": _h_pr_closed,
    "port.applied": _h_port_applied,
    "flow.chosen": _h_flow_chosen,
    "release.opened": _h_release_opened,
    "release.tagged": _h_release_tagged,
    "release.closed": _h_release_closed,
    "bug.found": _h_bug_found,
    "bug.fixed": _h_bug_fixed,
    "lesson.recorded": _h_lesson,
    "research.recorded": _h_research,
    "decision.recorded": _h_decision,
    "decision.superseded": _h_decision_superseded,
    "memory.recorded": _h_memory,
    "job.started": _h_job_started,
    "external.observed": _h_external,
    "job.ended": _h_job_ended,
    "memory.forgotten": _h_memory_forgotten,
    "session.started": _h_session_started,
    "session.prompt": _h_session_prompt,
    "session.note": _h_session_note,
    "session.ended": _h_session_ended,
    "gate.out_of_order": _h_gate_out_of_order,
    "cadence.ran": _h_cadence,
}


def known_kinds() -> frozenset[str]:
    """The event vocabulary, DERIVED from `HANDLERS`.

    Declaring it separately would make the vocabulary and its interpretation two lists that
    nothing forces to agree — a kind could be declared and never handled (folding silently
    to "nothing happened"), or handled and never declared (rejected at append time).

    Lives HERE, next to the handlers it derives from, rather than in `events`. It was in
    `events` with a lazy `from .model import HANDLERS`, which made the two a mutually
    importing pair held apart by one deferred import; `model` imports `Event` eagerly
    because it is in every signature, so one eager edge already existed and a second would
    have been an ImportError at startup. Its only consumer is `infra/log.py`'s append-time
    check, and `infra` may import `core` — so the derivation moves to the owner of the
    data and the cycle is gone rather than balanced (B127).
    """
    return frozenset(HANDLERS)


def fold(events: list[Event], *, strict: bool = True) -> State:
    """Replay events into state. Pure; no I/O; deterministic.

    ``strict`` raises on an unknown kind. Non-strict is for reading a log written by a
    NEWER ddflow than this one, where forward compatibility beats correctness of the
    unknown part -- but it counts what it skipped so the caller can refuse to act.
    """
    st = State()
    for ev in events:
        st.event_count += 1
        st.last_lamport = max(st.last_lamport, ev.lamport)
        handler = HANDLERS.get(ev.kind)
        if handler is None:
            if strict:
                raise ValueError(f"unknown event kind {ev.kind!r} at lamport {ev.lamport}")
            st.skipped_kinds[ev.kind] = st.skipped_kinds.get(ev.kind, 0) + 1
            continue
        handler(st, ev)
    return st
