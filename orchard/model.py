"""Domain model and the fold — the deterministic projection of the event log.

``fold(events) -> State`` is a pure function. Same events in, same state out, on any
machine, in any process, at any time. Every rule in this module is therefore testable
without touching a disk, and ``orchard rebuild`` is just ``fold`` over everything.

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
        return [
            i
            for i in self.items.values()
            if i.kind == "task" and not i.removed and (not phase or i.parent == phase)
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


def _h_added(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    it.kind = kind
    it.title = d.get("title", it.title)
    it.parent = d.get("parent", it.parent)
    it.needs = list(d.get("needs", it.needs))
    it.globs = list(d.get("globs", it.globs))
    it.body = d.get("body", it.body)
    it.tags = list(d.get("tags", it.tags))
    it.priority = int(d.get("priority", it.priority))
    it.removed = False


def _h_updated(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    for f in ("title", "parent", "body", "blocked_reason"):
        if f in d:
            setattr(it, f, d[f])
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
    it.lease.renewed_at = float(d.get("at", it.lease.renewed_at))
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
    it = st.items.get(ev.subject)
    if it:
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
    st.bugs[ev.subject] = Bug(
        id=ev.subject,
        item=ev.data.get("item", ""),
        summary=ev.data.get("summary", ""),
        found_at=ev.ts,
    )


def _h_bug_fixed(st: State, ev: Event) -> None:
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.fixed_at = ev.ts
    bug.regression_test = ev.data.get("regression_test", "")
    bug.lesson = ev.data.get("lesson", "")


def _h_lesson(st: State, ev: Event) -> None:
    d = ev.data
    prev = st.lessons.get(ev.subject)
    st.lessons[ev.subject] = Lesson(
        id=ev.subject,
        title=d.get("title", ""),
        rule=d.get("rule", ""),
        why=d.get("why", ""),
        how=d.get("how", ""),
        seen_in=list(d.get("seen_in", [])),
        tags=list(d.get("tags", [])),
        at=ev.ts,
    )
    for sid in d.get("supersedes", []):
        if sid in st.lessons:
            st.lessons[sid].superseded_by = ev.subject
    if prev and prev.seen_in:
        st.lessons[ev.subject].seen_in = list(
            dict.fromkeys(prev.seen_in + st.lessons[ev.subject].seen_in)
        )


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
        budget=d.get("budget", ""),
        at=ev.ts,
        item=d.get("item", ""),
    )


def _session(st: State, ev: Event) -> Session:
    return st.sessions.setdefault(ev.subject, Session(id=ev.subject, agent=ev.agent))


def _h_session_started(st: State, ev: Event) -> None:
    st.sessions[ev.subject] = Session(
        id=ev.subject, agent=ev.agent, model=ev.data.get("model", ""), started_at=ev.ts
    )


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
    _session(st, ev).notes.append(
        {"at": ev.ts, "text": ev.data.get("text", ""), "item": ev.data.get("item", "")}
    )


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
    NEWER Orchard than this one, where forward compatibility beats correctness of the
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
