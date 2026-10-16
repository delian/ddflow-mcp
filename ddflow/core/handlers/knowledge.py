"""Fold handlers: bugs, lessons, decisions, research and memory.

`model.HANDLERS` maps each event kind to an `_h_*` function here -- directly, or through
the small wrapper or factory call it shows. Everything in this module is pure."""

from __future__ import annotations

from typing import Any

from ..events import Event, changelog_of
from ..records import Bug, Decision, Lesson, Memory, ResearchNote, State


def _definition(ev: Event) -> dict[str, Any]:
    d = ev.data
    return {
        "event": ev.id or ev.compute_id(),
        "agent": ev.agent,
        "lamport": ev.lamport,
        "ts": ev.ts,
        "title": str(d.get("title") or d.get("question") or ""),
        "data": dict(d),
    }


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same definition: the add-time `dedupe` check ran on each clone and may differ."""
    return {k: v for k, v in a.items() if k != "dedupe"} == {
        k: v for k, v in b.items() if k != "dedupe"
    }


def _authors(definition: dict[str, Any]) -> list[str]:
    return definition.get("authors") or [definition["agent"]]


def _contest(st: State, ev: Event, kind: str, current: Any) -> str:
    """Whether ``ev`` defines ``kind:id`` plainly (``""``), contests it (``"contested"``),
    or settles a contest (``"resolved"``).

    B230e852e10: a decision, lesson or research note recorded under one id by two agents
    used to fold last-writer-wins, and the decision handler mixed the two definitions
    field by field. The same rule as a rival `task.added` (B191) applies: a DIFFERENT
    definition from ANOTHER agent is a contest, kept whole beside the first and never
    merged; an identical one is not. The same agent re-recording its own is a revision
    and merges as it always did. A record carrying ``resolves`` (written by `ddflow
    resolve`) names the kept definition and replaces the contest. Older ddflow folds that
    event as an ordinary re-record, so it needs no new event kind.
    """
    key = f"{kind}:{ev.subject}"
    mine = _definition(ev)
    d = ev.data
    if d.get("resolves"):
        # The kept rival's own definition stands, not the resolver's envelope: a later
        # revision by its author must read as a revision, not as a rival of the resolver.
        kept = next(
            (r for r in (current.contested if current else []) if r["event"] == d.get("kept")), None
        )
        st.record_definitions[key] = kept or mine
        return "resolved"
    prior = st.record_definitions.get(key)
    if current is not None and current.contested:
        rivals = current.contested
        for i, r in enumerate(rivals):
            if r["agent"] == ev.agent:
                rivals[i] = mine  # a rival revising its own definition
                break
        else:
            if not any(_same(r["data"], d) for r in rivals):
                rivals.append(mine)
        if all(_same(r["data"], d) for r in rivals):
            # The rivals converged on one definition: nothing is disputed any more.
            current.contested = []
            st.record_definitions[key] = {**mine, "authors": sorted({r["agent"] for r in rivals})}
            return ""
        return "contested"
    if current is not None and prior is not None and ev.agent not in _authors(prior):
        if _same(prior["data"], d):
            # The same definition again: nothing is lost, but it now has two authors, so a
            # later revision by either is a revision, not a rival.
            prior["authors"] = sorted({*_authors(prior), ev.agent})
            return ""
        current.contested = [prior, mine]
        return "contested"
    st.record_definitions[key] = mine
    return ""


def _kept_at_by(prev: Any, ev: Event) -> tuple[str, str]:
    """(at, by) of the definition a settlement keeps: the kept one's own, so the rebuilt
    record reads as that agent wrote it, not as the agent that ran `resolve`."""
    kept = next((c for c in prev.contested if c["event"] == ev.data.get("kept")), None)
    return (kept["ts"], kept["agent"]) if kept else (prev.at or ev.ts, prev.by or ev.agent)


def _h_bug_found(st: State, ev: Event) -> None:
    """Merge, never replace. Folding `bug.found` after `bug.fixed` used to clear
    `fixed_at`, so a bug that was fixed (with its regression test) read as open."""
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.item = ev.data.get("item", "") or bug.item
    bug.summary = ev.data.get("summary", "") or bug.summary
    bug.title = ev.data.get("title", "") or bug.title
    bug.severity = ev.data.get("severity", "") or bug.severity
    bug.scope = ev.data.get("scope", "") or bug.scope  # absent: the default, `project`
    bug.fix_task = ev.data.get("fix_task", "") or bug.fix_task
    bug.repro = dict(ev.data.get("repro") or {}) or bug.repro
    bug.globs = list(ev.data.get("globs") or bug.globs)
    bug.found_at = bug.found_at or ev.ts


def _h_bug_reported_upstream(st: State, ev: Event) -> None:
    """A report about a ddflow-scoped bug went (or was prepared) upstream. Merge, never
    blank: a later event fills what an earlier one (say, a prepared report with no url
    yet) left empty, and an empty field never blanks one that is set."""
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    d = ev.data
    bug.upstream_url = str(d.get("url", "") or "") or bug.upstream_url
    bug.upstream_number = str(d.get("number", "") or "") or bug.upstream_number
    bug.upstream_delivery = str(d.get("delivery", "") or "") or bug.upstream_delivery
    bug.upstream_digest = str(d.get("digest", "") or "") or bug.upstream_digest
    # Only a recorded `sent_at` says it went: a prepared report has none yet.
    bug.upstream_sent_at = str(d.get("sent_at", "") or "") or bug.upstream_sent_at


def _h_bug_fixed(st: State, ev: Event) -> None:
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.fixed_at = ev.ts
    bug.regression_test = ev.data.get("regression_test", "")
    bug.regression_tests = list(
        ev.data.get("regression_tests") or ([bug.regression_test] if bug.regression_test else [])
    )
    bug.lesson = ev.data.get("lesson", "")
    # A keyless event (an older writer, a re-close) leaves a recorded line alone.
    bug.changelog = changelog_of(ev.data.get("changelog")) or bug.changelog


def _h_bug_invalid(st: State, ev: Event) -> None:
    """The first closure as invalid is the one kept: `api.bug_invalid` refuses a second,
    so two can only meet from clones that each closed it, and the reason recorded first
    is the one the record already showed. Never touches `fixed_at` -- see `Bug.resolution`."""
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    if bug.invalid_at:
        return
    bug.invalid_at = ev.ts
    bug.invalid_reason = ev.data.get("reason", "")
    bug.evidence = ev.data.get("evidence", "")


def _h_bug_reopened(st: State, ev: Event) -> None:
    """Undo a closure, fixed or invalid (B7bdcc6b212: a completion closed bugs it never
    fixed, and neither `bug found` nor `bug invalid` could say so). The closure's events
    stay in the log; the record reads open again, and ``fix_task`` is what the writer
    decided (`api.bug_reopen`), "" when the old one cannot fix it."""
    bug = st.bugs.setdefault(ev.subject, Bug(id=ev.subject))
    bug.fixed_at = bug.regression_test = bug.lesson = ""
    bug.regression_tests = []
    bug.changelog = {}
    bug.invalid_at = bug.invalid_reason = bug.evidence = ""
    bug.fix_task = str(ev.data.get("fix_task", bug.fix_task) or "")
    bug.reopened_at = ev.ts
    bug.reopen_reason = ev.data.get("reason", "")


def _h_lesson(st: State, ev: Event) -> None:
    """Merge, never replace — the same rule `_h_decision` and `_h_bug_found` follow.

    Shard merges reorder, so a re-record of a lesson can fold AFTER the supersession
    that retired it. Rebuilding the object wholesale dropped `superseded_by`, and the
    retired lesson walked back into the reconstruction brief's standing knowledge and
    into `recall` — advice the project had explicitly replaced, presented as current.
    """
    d = ev.data
    prev = st.lessons.get(ev.subject)
    mode = _contest(st, ev, "lesson", prev)
    if mode == "contested":
        return
    if mode == "resolved" and prev is not None:
        at, by = _kept_at_by(prev, ev)
        prev = Lesson(id=ev.subject, superseded_by=prev.superseded_by, at=at, by=by)
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
        by=prev.by if prev and prev.by else ev.agent,
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
    mode = _contest(st, ev, "decision", prev)
    if mode == "contested":
        return
    if mode == "resolved" and prev is not None:
        at, by = _kept_at_by(prev, ev)
        prev = Decision(id=ev.subject, superseded_by=prev.superseded_by, at=at, by=by)
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
        by=prev.by if prev and prev.by else ev.agent,
        saw=list(d["saw"]) if d.get("saw") is not None else (prev.saw if prev else None),
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
    # Replace-on-record already (no merge), so only the contest needs deciding here.
    prev = st.research.get(ev.subject)
    mode = _contest(st, ev, "research", prev)
    if mode == "contested":
        return
    at = _kept_at_by(prev, ev)[0] if mode == "resolved" and prev is not None else ev.ts
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
        at=at,
        item=d.get("item", ""),
    )


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
