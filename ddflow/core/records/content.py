"""The content records: bugs, lessons, decisions, research, memory, jobs, schedules,
sessions, links and releases.

Part of the former `ddflow/core/records.py`, split into submodules that stay under
30k chars each (rule r-small-files). These are the records that are CONTENT rather
than items or gate vocabulary: what was found, learned, decided, remembered, run,
scheduled and linked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


#: How many `bug.found` sightings make an open bug read CONFIRMED again even while every
#: repro run so far exited 0 (DX-bug-repro-verify, "until a second sighting or a
#: confirming run"): the filing was the first, so the second one confirms.
SIGHTINGS_THAT_CONFIRM = 2


@dataclass
class Bug:
    id: str
    item: str = ""
    summary: str = ""
    found_at: str = ""
    fixed_at: str = ""
    regression_test: str = ""
    #: The tests one by one (B227585c781). A bug closed before the list was recorded
    #: has its whole `regression_test` string as the single entry.
    regression_tests: list[str] = field(default_factory=list)
    lesson: str = ""
    #: Optional `bug.fixed` `changelog` {category, line, skip}; {} when none was given.
    changelog: dict[str, Any] = field(default_factory=dict)
    #: Closed as a FALSE finding (`bug invalid`), with why and what showed it. Kept apart
    #: from `fixed_at` because the two closures claim different things: a fix claims a
    #: repair and a regression test that failed without it; an invalid finding claims
    #: there was nothing to repair. B97355c6d15 was shown false by a test and could only
    #: be closed as "fixed", which would have written a repair nobody made into history.
    invalid_at: str = ""
    invalid_reason: str = ""
    evidence: str = ""
    #: `bug found --evidence` (the MCP tool's agent-supplied "what showed the bug"):
    #: stored, never run. Kept apart from `evidence` (the `bug invalid` probe that showed
    #: the finding FALSE): the two claim different things and a found-evidence must not be
    #: dropped when a later invalid closure records its own probe.
    found_evidence: str = ""
    #: Optional, from `bug found --title/--severity/--scope`; an old event carries none
    #: and folds to "" (scope: `project`, the default).
    title: str = ""
    severity: str = ""
    scope: str = "project"
    #: The queue item that fixes this bug (`bug.found` `fix_task`: the task `bug found`
    #: filed, or the open bug-fix task the report named). Its completion requires the
    #: bug closed; "" for a bug with no task (`--no-task`, or filed before fix tasks were).
    #: A report filed against an open fix task links to it here without being one of the
    #: task's `fixes`: that task's completion leaves it open (B7bdcc6b212).
    fix_task: str = ""
    #: `bug found --globs`: the files its fix touches, carried to the fix task and its claim.
    globs: list[str] = field(default_factory=list)
    #: `bug found --tags` and `bug tag` (`bug.tagged`): plain strings, namespaced ones
    #: (`ns:x`) included; an old event carries none and folds to []. `wave:*` is derived.
    tags: list[str] = field(default_factory=list)
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)
    #: `bug.reported_upstream`: where the report about this bug went (a ddflow bug filed
    #: against ddflow itself).
    upstream_url: str = ""
    upstream_number: str = ""
    upstream_delivery: str = ""
    upstream_sent_at: str = ""
    upstream_digest: str = ""
    #: `bug.found` `repro`: the link to the reproduction bundle ({ref, bytes, mode, truncated}).
    repro: dict[str, Any] = field(default_factory=dict)
    #: `bug.found` `sighted`: what the bug's repro command said the last time it ran
    #: ("" none). "yes" once any sighting's run failed -- it is the sighting that
    #: CONFIRMED the bug, and no later non-failing run unconfirms it; "no" while every
    #: run so far exited 0, which makes an open bug UNCONFIRMED (DX-bug-repro-verify).
    reproduced: str = ""
    #: The counts of the last sighting that ran (`bug.found` `sighted` runs/failures):
    #: how many runs reached an exit code and how many of them failed.
    repro_runs: int = 0
    repro_failures: int | None = None  # None: not recorded; "yes" repro folded needs it said
    #: How many `bug.found` sightings the bug has (every `bug.found` event counts, run
    #: or not): 1 is the filing, 2 makes a second sighting, which reads confirmed-open
    #: even while every run so far exited 0 (DX-bug-repro-verify, "until a second
    #: sighting or a confirming run").
    sightings: int = 0
    #: `bug.reopened`: the last time a closure was undone, and why (B7bdcc6b212).
    reopened_at: str = ""
    reopen_reason: str = ""

    @property
    def resolution(self) -> str:
        """'fixed', 'invalid' or '' (open). A fix wins over an invalid closure whatever the
        fold order: a finding later shown real and fixed is fixed, and a shard merge that
        sorts the older `bug.invalid` last must not turn it back into a false finding."""
        if self.fixed_at:
            return "fixed"
        return "invalid" if self.invalid_at else ""

    @property
    def open(self) -> bool:
        return not self.resolution


def contested_summary(contested: list[dict[str, Any]]) -> str:
    """The rivals of a contested decision, lesson or research record, one line; "" if none."""
    if not contested:
        return ""
    return (
        f"{len(contested)} definitions from different agents ("
        + "; ".join(f"{d['event'][:12]} by {d['agent']}: {d['title']!r}" for d in contested)
        + ")"
    )


@dataclass
class Lesson:
    id: str
    title: str = ""
    rule: str = ""
    why: str = ""
    how: str = ""
    seen_in: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)
    at: str = ""
    #: Rival definitions of this id from different agents (B230e852e10), each {"event",
    #: "agent", "lamport", "ts", "title", "data"}; empty unless contested. While it is
    #: not empty the displayed fields are the FIRST definition's, never mixed with a
    #: rival's, and the record is not in force until `ddflow resolve` settles it.
    contested: list[dict[str, Any]] = field(default_factory=list, metadata={"omit_empty": True})
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
    #: The agent id on the event that first recorded it: who a reader is trusting
    #: (`core/provenance.py`). Not a field an event carries -- the event's own `agent`.
    by: str = ""

    @property
    def live(self) -> bool:
        """In force: not retired by a later lesson, and not in an unresolved contest."""
        return not self.superseded_by and not self.contested

    def text(self) -> str:
        return "\n".join(x for x in (self.title, self.rule, self.why, self.how) if x)


#: How a record points at another. `distinct` is a dismissal -- "I looked, these are
#: different" -- kept so the same pair is not asked about again; the others are links.
LINK_RELATIONS = ("extends", "duplicate_of", "related", "distinct")
#: The ones an ADD event can carry as a field (`distinct` is only ever said afterwards).
ADD_RELATIONS = ("extends", "duplicate_of", "related")


@dataclass
class RecordLinks:
    """What the log says about how one record relates to others, and what was added to it.

    ACCUMULATED, never replaced (decision D-no-duplicates, 2): every entry is keyed by the
    event that made it, so the fold is commutative and idempotent -- two additions made
    at once by two clones both survive in any order, and a re-delivered event is one entry.
    That is why an addition is its own event kind: a field on `task.updated` or `bug.found`
    keeps the last writer and loses the other (and once overwrote a bug's summary).
    """

    id: str
    #: event id -> {event, text, who, at, score}: verbatim additions (`record.extended`).
    additions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: event id + relation + target -> {event, relation, target, by, at, score, source}.
    link_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: add event id -> the answer recorded with it: {answer, score, candidates, at}.
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def extensions(self) -> list[dict[str, Any]]:
        """The additions in one order whatever order they folded in: oldest first."""
        return sorted(self.additions.values(), key=lambda a: (a["at"], a["event"]))

    def _sorted(self, entries) -> list[dict[str, Any]]:
        return sorted(entries, key=lambda x: (x["at"], x["event"], x["target"]))

    @property
    def links(self) -> list[dict[str, Any]]:
        """Every relation but `distinct` (those are `dismissals`), including relations this
        ddflow does not know: extends / duplicate_of / related and whatever a newer one says."""
        return self._sorted(x for x in self.link_entries.values() if x["relation"] != "distinct")

    @property
    def dismissals(self) -> list[dict[str, Any]]:
        """The 'distinct' entries: pairs somebody looked at and judged different."""
        return self._sorted(x for x in self.link_entries.values() if x["relation"] == "distinct")

    @property
    def answer(self) -> dict[str, Any]:
        """The recorded dedupe answer of the NEWEST add that carried one."""
        if not self.answers:
            return {}
        latest = max(self.answers, key=lambda e: (self.answers[e].get("at", ""), e))
        return self.answers[latest]

    def dismissed(self) -> set[str]:
        """Records this one was judged DISTINCT from."""
        return {x["target"] for x in self.link_entries.values() if x["relation"] == "distinct"}

    def linked(self, relation: str = "") -> set[str]:
        return {
            x["target"]
            for x in self.link_entries.values()
            if x["relation"] != "distinct" and (not relation or x["relation"] == relation)
        }


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
class Schedule:
    """A scheduled job's DEFINITION: what runs, how often, on what, and how it may act
    (decision D-sched-no-daemon). When it is due and what a run did are not here: due
    state is computed from the log, and a run is recorded by `cadence.ran` under the
    job's id, as the existing periodic passes always were.

    Folded from `schedule.defined` / `schedule.updated` / `schedule.removed`. The other
    sources -- `.ddflow/schedules/*.toml` and the `[cadence]` config -- are read by
    `services.schedule`, which merges all three into the one view every surface shows.
    """

    id: str
    title: str = ""
    #: Exactly one of {"every_days": float, "every_tasks": int, "every_phases": int}.
    cadence: dict[str, Any] = field(default_factory=dict)
    #: Upstream job ids: this job is due only after their latest run succeeded since its own.
    needs: list[str] = field(default_factory=list)
    #: What a run may write. Two jobs whose scopes overlap never run at once.
    scope_globs: list[str] = field(default_factory=list)
    #: Jobs sharing a non-empty group never run at once, whatever their scopes.
    concurrency_group: str = ""
    #: The prompt template a run renders (`ddflow prompts`); "" means the job's id.
    prompt: str = ""
    mode: str = "report"  # report | fix
    #: {"max_items", "max_bugs", "max_turns"}: non-negative ints, 0 = no cap.
    budget: dict[str, int] = field(default_factory=dict)
    #: A run that cannot finish files a needs-operator item rather than failing quietly.
    escalate: bool = True
    missed: str = "skip"  # skip | once -- never a catch-up of every missed period
    #: Minutes a due run may be spread by, so jobs due together do not all start at once.
    jitter: int = 0
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    at: str = ""
    by: str = ""
    #: Why it was removed; "" while defined. A removed job is kept, like a forgotten memory.
    removed: str = ""


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
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)
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
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)
    #: Where this decision came from: an ADR path, a URL, a commit sha. `Lesson` has
    #: `seen_in` and `ResearchNote` has `sources`; a decision had only the prose
    #: `context`, so the import's vanished-source check could cover items, lessons,
    #: research and notes -- and not decisions. Parsing a path back out of a sentence
    #: is the anti-pattern that check exists to replace, so the field is the fix.
    sources: list[str] = field(default_factory=list)
    status: str = "accepted"  # proposed | accepted | superseded
    decided_by: str = ""  # operator | agent | a name
    supersedes: list[str] = field(default_factory=list)
    #: Rival definitions of this id from different agents (B230e852e10), each {"event",
    #: "agent", "lamport", "ts", "title", "data"}; empty unless contested. While it is
    #: not empty the displayed fields are the FIRST definition's, never mixed with a
    #: rival's, and the record is not in force until `ddflow resolve` settles it.
    contested: list[dict[str, Any]] = field(default_factory=list, metadata={"omit_empty": True})
    superseded_by: str = ""
    at: str = ""
    item: str = ""
    #: The agent id on the event that first recorded it (`core/provenance.py`); distinct
    #: from `decided_by`, which is what the recorder CLAIMED about who decided.
    by: str = ""
    #: What the author SAW when recording it (B0dcccd8594): the ids of the live decisions
    #: whose globs overlapped this one's at write time. ``None`` when the event has no
    #: `saw` key (every log written before), which never forms an unreconciled pair; ``[]``
    #: means the author saw no overlapping decision. Two live decisions on overlapping
    #: globs where neither names the other were written concurrently: the fold is pure and
    #: cannot tell that from the lamports alone, so the author's knowledge is recorded.
    saw: list[str] | None = field(default=None, metadata={"omit_empty": True})

    @property
    def live(self) -> bool:
        return self.status == "accepted" and not self.superseded_by and not self.contested

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
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)
    #: Rival definitions of this id from different agents (B230e852e10), each {"event",
    #: "agent", "lamport", "ts", "title", "data"}; empty unless contested. While it is
    #: not empty the displayed fields are the FIRST definition's, never mixed with a
    #: rival's. `doctor` and `show` surface the contest; a note has no in-force notion.
    #: `ddflow resolve` settles it.
    contested: list[dict[str, Any]] = field(default_factory=list, metadata={"omit_empty": True})
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
    #: `session end --summary`: what the session did, in its own words (B194). The last
    #: non-empty one wins, so a bare `session.ended` never blanks it.
    summary: str = ""
    #: `record.corrected` audit entries, oldest first: who, why and digests of the old and the
    #: new value of each field, never the old value (REC-correct-event).
    corrections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class FoldProblem:
    """One event a non-strict fold could not apply (B-uni-compat-events): its handler or its
    upcaster raised. The fold records it and goes on, so one malformed event no longer hides
    every event after it; `ddflow doctor` names each one. Whatever the handler had already
    changed before it raised stays changed -- the fold does not copy the state per event."""

    event: str
    kind: str
    lamport: int
    agent: str
    error: str
