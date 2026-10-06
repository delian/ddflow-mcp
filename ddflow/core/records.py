"""The records the fold produces: the item states, every record dataclass, and `State`.

`model.fold` builds a `State` by running `model.HANDLERS` (`core/handlers/`) over the
events; `ddflow.core.model` re-exports every name here."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .events import version_key

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


#: A claim's TTL when its event carries none -- the shipped `[lease] ttl_s`.
DEFAULT_LEASE_TTL_S = 1800


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
    #: The item's state when this lease was granted. A release hands the item back to
    #: it: a claim refused on a crashed (RUNNING) item must leave it RUNNING, so recovery
    #: still sees the crash, while a deliberate release of a normal claim reopens it.
    prior_state: str = ""
    #: Who held the item before this lease (a crashed holder's expired lease), so a
    #: holder releasing its OWN re-claimed work hands it back, not someone else's crash.
    prior_holder: str = ""
    #: An `item.started` folded under this lease: the claim went through and the holder
    #: took the item on (a refused claim is undone before `item.started` is written).
    started: bool = False

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
    #: The forge's merge queue (B172): "queued" while the request has an entry, with its
    #: position and the queue's own state word. An open request that WAS queued and is not
    #: now was ejected -- `pr sync` says so when it sees the change.
    queue: str = ""
    queue_position: int = 0
    queue_state: str = ""


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
    #: The author's triage of a review's findings: gate -> finding digest -> {verdict,
    #: probe, n, severity, title, location, by, at}. Keyed by the DIGEST of the finding's
    #: text and kept apart from `gates`, which a re-review replaces wholesale: a triage
    #: therefore carries over to a later review exactly when the finding is word for
    #: word the same (decision D-review-triage).
    triage: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
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
    #: Optional `item.completed` `changelog` {category, line, skip}; {} when none was given.
    changelog: dict[str, Any] = field(default_factory=dict)
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
    #: The bugs this task was filed to fix (`task.added` `fixes`, by `bug found`). The bug
    #: record's `fix_task` is the binding side: `complete` asks the bugs, not this list.
    fixes: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    created_at: str = ""
    completed_at: str = ""
    #: Each time verification sent a completed item back: {"at", "by", "reason", "claims",
    #: "forced"}, oldest first. The completion's gates are cleared with it, so the work is
    #: re-done and re-gated; the log keeps what they were.
    reopened: list[dict[str, Any]] = field(default_factory=list)
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
    #: against the entry. Unbounded: see `_displace`.
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

    def lease_candidates(self) -> list[dict[str, Any]]:
        """The claims `resolve --keep` may name: every contestant, and the displayed lease
        when it is not one -- the current holder, which overlapped none of them, can win
        the contest too (B-resolve-cannot-keep-holder). Empty without a lease contest."""
        if not self.lease_contest:
            return []
        cur = self.lease
        if cur is None or any(h["event"] == cur.event for h in self.lease_contest):
            return list(self.lease_contest)
        return [*self.lease_contest, _claim(cur)]

    def lease_losers(self, kept: dict[str, Any]) -> list[dict[str, Any]]:
        """The claims `resolve` releases when ``kept`` wins: every one that overlapped it,
        and the displayed lease if that is another claim -- ``kept`` is about to take the
        item from it. A contestant that met neither lost nothing to ``kept``.

        Keeping the displayed lease when it is not a contestant releases EVERY contestant:
        it overlapped none of them -- the fold displays the later claim, so they had
        lapsed when it took the item -- and they all lost the item to it. Without this
        the current holder could not win a contest it was never in, and the only ways
        out took the item from it and handed it back to a lapsed claim."""
        cur = self.lease
        if (
            cur is not None
            and cur.event == kept["event"]
            and all(h["event"] != cur.event for h in self.lease_contest)
        ):
            return list(self.lease_contest)
        losers = self.lease_clashes(kept)
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
    #: `bug.reported_upstream`: where the report about this bug went (a ddflow bug filed
    #: against ddflow itself).
    upstream_url: str = ""
    upstream_number: str = ""
    upstream_delivery: str = ""
    upstream_sent_at: str = ""
    upstream_digest: str = ""
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
    #: The agent id on the event that first recorded it: who a reader is trusting
    #: (`core/provenance.py`). Not a field an event carries -- the event's own `agent`.
    by: str = ""

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
    #: The agent id on the event that first recorded it (`core/provenance.py`); distinct
    #: from `decided_by`, which is what the recorder CLAIMED about who decided.
    by: str = ""

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
    #: `session end --summary`: what the session did, in its own words (B194). The last
    #: non-empty one wins, so a bare `session.ended` never blanks it.
    summary: str = ""


@dataclass
class State:
    #: `ci.result`: the last CI_RESULTS_KEPT outcomes of the CI command, oldest first.
    ci_results: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, Item] = field(default_factory=dict)
    bugs: dict[str, Bug] = field(default_factory=dict)
    lessons: dict[str, Lesson] = field(default_factory=dict)
    research: dict[str, ResearchNote] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    memories: dict[str, Memory] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    #: job id -> its definition from `schedule.*` events (`services.schedule` merges the
    #: file and `[cadence]` sources over these).
    schedules: dict[str, Schedule] = field(default_factory=dict)
    #: trigger id -> its last TRIGGER_FIRES_KEPT fires, oldest first: {"at", "key", "items",
    #: "hop", "digest", "job", "events"} (`trigger.fired`). Every fire is in the log; this
    #: tail is what the breaker and the global hourly cap read.
    trigger_fires: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: trigger id -> dedupe key -> {"at"} of that key's latest fire: what the cooldown
    #: reads (the breaker and the hourly cap read the `trigger_fires` tail; which items are
    #: open is `trigger_items`).
    trigger_keys: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: trigger id -> its last TRIGGER_SUPPRESSIONS_KEPT suppressions: {"at", "key", "reason",
    #: "detail"} (`trigger.suppressed`). Every one is in the log; the state keeps the tail.
    trigger_suppressed: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: item id -> the fire that filed it: {"trigger", "key", "hop"}. How a trigger knows
    #: its own output (never a source) and how deep a remediation chain has gone.
    trigger_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: `trigger.evaluated`: the last TRIGGER_RUNS_KEPT evaluator runs, oldest first.
    trigger_runs: list[dict[str, Any]] = field(default_factory=list)
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
    #: "<item>><branch>" -> a gitflow hotfix's BACK-MERGE request (B171), as {"item", "into",
    #: "number", "url", "forge", "state"}; state is open | merged | closed. The item itself
    #: completes once production has the fix; this is what remembers develop still needs it.
    back_merges: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: knob -> the recorded workflow CHOICE: {"value", "by", "agent", "user", "at",
    #: "reason"}. `by` is "explicit" or "default" -- a default applied at first use is
    #: recorded so the project keeps following it even if ddflow's default changes.
    flow_choices: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: environment -> the last DEPLOY a hook reported (`promote deployed`, B183):
    #: {"sha", "at", "agent"}. A promotion records what the branch says; this is what runs.
    deployments: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: item id -> the add that currently DEFINES it, in the shape of an `Item.contested`
    #: entry. What a rival add is compared against, and what becomes the first side of
    #: the contest when one arrives.
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: reviewer-entry digest -> who a TOOL wrote it as ({"name", "kind", "agent",
    #: "person", "user", "at"}), and digest -> the person who approved it. A digest in
    #: the first and not the second is a reviewer whose reviews do not count yet
    #: (decision D-reviewer-trust). An entry no tool wrote appears in neither.
    reviewer_writes: dict[str, dict[str, Any]] = field(default_factory=dict)
    reviewer_approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: document kind -> who last enabled or disabled its export and when, and whether the
    #: operator vetoed it: {"enabled", "by", "human", "at", "path", "mode", "local",
    #: "locked", "acked"}. The SELECTION itself lives in the config; this is the record
    #: of who changed it (decision D-export-agent-enable).
    exports: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: record id -> its links and additions. Only records that have any appear.
    links: dict[str, RecordLinks] = field(default_factory=dict)
    last_lamport: int = 0
    event_count: int = 0
    #: kind -> count, for events a non-strict fold could not interpret. Counted rather
    #: than ignored so a caller can refuse to act on a partially-understood log.
    skipped_kinds: dict[str, int] = field(default_factory=dict)
    #: ddflow version -> who has worked on this log under it: {"agents", "at", "install"}
    #: (`ddflow.seen`, decision D-upgrade-event-kinds). The project's version is the highest
    #: key under `events.version_key`, not the latest write -- an older ddflow stamping after
    #: a newer one does not lower it.
    ddflow_versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Session-scoped skew overrides (`skew.overridden`), oldest first: {"agent", "session",
    #: "running", "log_version", "reason", "at"}.
    skew_overrides: list[dict[str, Any]] = field(default_factory=list)
    #: Upgrades applied (`upgrade.applied`), oldest first: {"from", "to", "categories",
    #: "backup", "agent", "at"}.
    upgrades: list[dict[str, Any]] = field(default_factory=list)
    #: version -> how many events were written by that OLDER ddflow under a skew override
    #: (the `older_ddflow` mark on their data).
    older_version_events: dict[str, int] = field(default_factory=dict)

    @property
    def highest_version(self) -> str:
        """The highest ddflow version that has stamped this log, "" when none has."""
        return max(self.ddflow_versions, key=lambda v: (version_key(v), v), default="")

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


# Claim-window helpers: `State`'s contest methods use them, and so do the lease handlers.
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
