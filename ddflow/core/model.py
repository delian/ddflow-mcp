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
from dataclasses import dataclass, field
from typing import Any

from .events import Event

# Item states. These are DERIVED, never written: an item's state is a function of the
# events about it. A state field that can be set directly is a field that can drift
# from the events that produced it, which is the whole defect class this design closes.
OPEN, RUNNING, BLOCKED, DONE, ABANDONED = "open", "running", "blocked", "done", "abandoned"

#: The single source for gate outcomes. Everything that validates, renders or maps an
#: outcome imports from here. Previously this tuple existed and nothing referenced it,
#: while five scattered literals did the real work -- so adding a sixth outcome meant
#: finding all five. A dead constant that LOOKS canonical is worse than none at all.
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
    #: Set when a `lease.expired` event was folded. The lease is KEPT so recovery can
    #: still see which worktree it pointed at.
    expired_at: str = ""

    def expired(self, now: float, grace_s: int = 0) -> bool:
        return (now - self.renewed_at) > (self.ttl_s + grace_s)

    def remaining_s(self, now: float) -> float:
        return (self.renewed_at + self.ttl_s) - now


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
    body: str = ""
    tags: list[str] = field(default_factory=list)
    priority: int = 100
    state: str = OPEN
    lease: Lease | None = None
    gates: dict[str, GateRecord] = field(default_factory=dict)
    worktree: str = ""
    branch: str = ""
    merged_sha: str = ""
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

    def gate_outcome(self, gate: str) -> str:
        rec = self.gates.get(gate)
        return rec.outcome if rec else ""


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

    def text(self) -> str:
        return "\n".join(x for x in (self.title, self.rule, self.why, self.how) if x)


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
    sessions: dict[str, Session] = field(default_factory=dict)
    cadences: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
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


def _h_added(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    it.kind = kind
    it.title = d.get("title", it.title)
    it.parent = _safe_parent(st, it.id, d.get("parent", it.parent))
    it.needs = list(d.get("needs", it.needs))
    it.globs = list(d.get("globs", it.globs))
    it.body = d.get("body", it.body)
    it.tags = list(d.get("tags", it.tags))
    it.priority = int(d.get("priority", it.priority))
    it.source = d.get("source", it.source)
    it.removed = False


def _h_updated(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    for f in ("title", "parent", "body", "blocked_reason"):
        if f in d:
            setattr(it, f, _safe_parent(st, it.id, d[f]) if f == "parent" else d[f])
    for f in ("needs", "globs", "tags"):
        if f in d:
            setattr(it, f, list(d[f]))
    if "priority" in d:
        it.priority = int(d["priority"])


def _h_removed(st: State, ev: Event, kind: str) -> None:
    _item(st, ev, kind).removed = True


def _h_lease_acquired(st: State, ev: Event) -> None:
    d = ev.data
    it = _item(st, ev, d.get("kind", "task"))
    it.lease = Lease(
        holder=d.get("holder", ev.agent),
        acquired_at=float(d.get("at", 0.0)),
        renewed_at=float(d.get("at", 0.0)),
        ttl_s=int(d.get("ttl_s", 1800)),
        worktree=d.get("worktree", ""),
        branch=d.get("branch", ""),
        globs=list(d.get("globs", [])),
        note=d.get("note", ""),
    )
    it.worktree = it.lease.worktree or it.worktree
    it.branch = it.lease.branch or it.branch


def _h_lease_renewed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if not it or not it.lease:
        return
    d = ev.data
    # Only the CURRENT holder may renew. Lamport values are computed independently on
    # unsynced clones, so merging two shards can order a former holder's renewal after
    # a later re-acquisition by someone else -- and applying it would point the live
    # lease at the dead agent's worktree, which every destructive path then targets.
    if "holder" in d and d["holder"] != it.lease.holder:
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
    if not it or not it.lease:
        return
    holder = ev.data.get("holder")
    if holder is not None and holder != it.lease.holder:
        return
    if ev.kind == "lease.expired":
        it.lease.ttl_s = 0
        it.lease.expired_at = ev.ts
        return
    it.lease = None


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
            it.merged_sha = ev.data.get("sha", it.merged_sha)
            # The importer has always written this -- `{"imported": True, "evidence":
            # "ticked in docs/todo.md:41"}` -- and the fold has always thrown it away,
            # so the one record of WHY an item was closed without running a single gate
            # existed only in the raw log. Third instance of this class in this series.
            it.completion_evidence = ev.data.get("evidence", it.completion_evidence)
        elif new_state == ABANDONED:
            it.blocked_reason = ev.data.get("reason", "")

    return handler


def _h_gate(outcome: str):
    def handler(st: State, ev: Event) -> None:
        d = ev.data
        it = _item(st, ev, d.get("kind", "task"))
        gate = d.get("gate", "")
        if outcome == "started":
            it.gates.setdefault(gate, GateRecord(gate=gate))
            it.gates[gate].at = ev.ts
        else:
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


def _h_worktree_merged(st: State, ev: Event) -> None:
    _item(st, ev, ev.data.get("kind", "task")).merged_sha = ev.data.get("sha", "")


def _h_worktree_removed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if it:
        it.worktree = ""


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
        seen_in=list(d.get("seen_in", [])),
        tags=list(d.get("tags", prev.tags if prev else [])),
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


def _h_cadence(st: State, ev: Event) -> None:
    st.cadences.setdefault(ev.subject, []).append(
        {
            "at": ev.ts,
            "by": ev.agent,
            "result": ev.data.get("result", ""),
            "evidence": ev.data.get("evidence", {}),
        }
    )


def _h_noop(st: State, ev: Event) -> None:
    """A marker with no state effect (e.g. `log.compacted`)."""


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
    "item.completed": _h_state(DONE),
    "item.abandoned": _h_state(ABANDONED),
    **{
        f"gate.{o}": _h_gate(o)
        for o in ("started", "passed", "failed", "unavailable", "partial", "skipped")
    },
    "worktree.created": _h_worktree_created,
    "worktree.merged": _h_worktree_merged,
    "worktree.removed": _h_worktree_removed,
    "bug.found": _h_bug_found,
    "bug.fixed": _h_bug_fixed,
    "lesson.recorded": _h_lesson,
    "research.recorded": _h_research,
    "decision.recorded": _h_decision,
    "decision.superseded": _h_decision_superseded,
    "session.started": _h_session_started,
    "session.prompt": _h_session_prompt,
    "session.note": _h_session_note,
    "session.ended": _h_session_ended,
    "cadence.ran": _h_cadence,
    "log.compacted": _h_noop,
}


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
