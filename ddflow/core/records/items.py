"""Work kinds, the lease, the pull-request snapshot, and the `Item` record.

Part of the former `ddflow/core/records.py`, split into submodules that stay under
30k chars each (rule r-small-files). This submodule holds what a unit of work is: the
kinds that run like a task, the lease that claims one, the forge request that parks
one, and the `Item` dataclass itself."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from .gates import ABANDONED, DONE, OPEN, GateOutcome, GateRecord, GateRun

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
    #: The clone that made the claim (`.ddflow/local/clone-id`); "" for a claim written
    #: before clones were recorded, or on a project that is not adopted. Two clones
    #: claiming under ONE name are rivals only when both say which clone they are
    #: (B36aa4c8ee5).
    clone: str = ""
    #: Globs granted OPTIMISTICALLY (DX-lease-hunk-optimistic): a live holder already
    #: overlaps them but a clean `git merge-tree` probe said the two branches merge
    #: cleanly, so this lease and that holder's stand together and the merge rebases the
    #: second instead of waiting. Generated/pinned (shared) files and real conflicts are
    #: never here.
    optimistic: list[str] = field(default_factory=list)

    def expired(self, now: float, grace_s: int = 0) -> bool:
        """Past its TTL plus grace, by the clock alone. Ask `live` to decide anything: a
        recorded expiry (`expired_at`) ends a lease at once, whatever the clock says."""
        return (now - self.renewed_at) > (self.ttl_s + grace_s)

    def live(self, now: float, grace_s: int = 0) -> bool:
        """The one answer to "does this lease still hold its item": no recorded expiry
        AND inside its TTL plus grace. A recorded expiry zeroes the TTL but keeps the
        lease, and the clock alone calls it live until the grace passes (B-uni-lease-api).
        """
        return not self.expired_at and not self.expired(now, grace_s)

    def remaining_s(self, now: float) -> float:
        return (self.renewed_at + self.ttl_s) - now

    def left_text(self, now: float, grace_s: int = 0, *, unit: str = "left") -> str:
        """The countdown as words. Past the TTL but inside the grace the lease is still
        live and `remaining_s` is negative; say it ran out, never a negative countdown
        (Bc99060b8ca, Ba3425a7aa8). Past the grace too, it simply ran out."""
        left = self.remaining_s(now)
        if left >= 0:
            return f"for another {left:.0f}s" if unit == "another" else f"{left:.0f}s left"
        if -left <= grace_s:
            return f"ran out {-left:.0f}s ago, in its grace period"
        return f"ran out {-left:.0f}s ago"


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


#: The item kinds that are units of work, claimed and merged like a task. They differ in
#: the gate pipeline `[gates].kind_pipelines` gives them; a subset of the registry in
#: `config_sections._kinds`. `task add --kind` writes one into `task.added`'s payload.
WORK_KINDS = ("task", "doc", "research", "job")


def work_kind(data: dict) -> str:
    """The kind a `task.added` payload names: a work kind, else a plain task (an older or
    newer writer's unknown kind folds as a task rather than breaking the fold)."""
    k = data.get("kind")
    return k if k in WORK_KINDS else "task"


@dataclass
class Item:
    """A phase or a task. One class, because every rule about scheduling,
    leasing, gating and recovery is identical for both — only the pipeline differs.
    Two near-identical classes would drift; this repo's own history is full of that."""

    id: str
    kind: str  # "phase" | one of WORK_KINDS
    title: str = ""
    parent: str = ""  # a task's phase; "" for a phase
    needs: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    #: True once an `updated` event named the globs (even to none): the item's own say, so a
    #: fix task with no globs of its own stops taking its bug's at claim (B5df7832d0a).
    globs_declared: bool = field(default=False, metadata={"internal": True})
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
    #: Every outcome ever recorded for this item's gates, oldest first (B-uni-gate-record.5):
    #: what the review budget, the fire rates and the repeated-failure detector count,
    #: folded once instead of scanning the log for each. Internal: not part of the item's
    #: wire form (`core.plain`), so a state snapshot would have to serialise it explicitly.
    gate_history: list[GateRun] = field(default_factory=list, metadata={"internal": True})
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
    #: The branch tip that `merge` landed: what proves a squash, which leaves no ancestry.
    merged_head: str = ""
    #: The merged sha a re-claim reopened (`item.reworked`), when this item is the rework
    #: of one that had already merged. The rework clears `merged_sha` so the fresh tree at
    #: base re-merges from scratch, but the PRIOR landing stays here so the gates that need
    #: it -- `implement` (no commit ahead) and `standards` (`--reviewed-sha`) -- can accept
    #: the already-landed work instead of refusing an empty branch (B600455cc2a).
    reopened_merge: str = field(default="", metadata={"omit_empty": True})
    #: Optional `item.completed` `changelog` {category, line, skip}; {} when none was given.
    changelog: dict[str, Any] = field(default_factory=dict)
    #: The branch the worktree forked from. Recorded because under gitflow and stacking it
    #: is no longer "the base branch" -- a hotfix forks from production, a stacked task
    #: from its dependency's branch -- and the merge target is derived from it.
    base: str = ""
    #: The commit `base` named when the worktree was created (the true fork). A review
    #: diffs against it when the live base ref has been history-rewritten since the claim,
    #: so the reviewer sees the item's own work, not the scrub's reverse changes (B05dce361fd).
    base_sha: str = ""
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
    #: The item's own paths inside its landed range, recorded with
    #: `update --landed-paths <globs>` for work that landed inside a MIXED commit
    #: (B738d7bbeab): `changed_paths` reports only these, so a commit that also changed
    #: code does not force the full task pipeline on a docs-only item. Each glob was
    #: verified inside the item's declared globs and inside the range's changed files.
    landed_paths: list[str] = field(default_factory=list, metadata={"omit_empty": True})
    #: `merge.reconciled`: this item's landing touched files another item changed since
    #: this one branched, so complete is refused until the affected tests pass on the
    #: reconciled tree (D-lease-hold-limit-r2). {} when the merge landed clean over nothing.
    #: Holds {files, base, main, at}: `at` is the reconcile event's timestamp, so a test pass
    #: recorded before the reconcile (a stale pre-merge run) never clears the guard.
    reconciled: dict[str, Any] = field(default_factory=dict)

    #: The bugs this task was filed to fix (`task.added` `fixes`, by `bug found`). The bug
    #: record's `fix_task` is the binding side: `complete` asks the bugs, not this list.
    fixes: list[str] = field(default_factory=list)
    #: Set on a generated GATE-SKIP follow-up (`task.added` `followup_of`, filed by a skipped
    #: gate): the item whose skipped gate it re-runs, and that gate's id. The one durable
    #: binding that lets abandoning or completing the item, or re-recording the gate
    #: non-skipped, settle the follow-up. Empty on every other item.
    followup_of: str = field(default="", metadata={"omit_empty": True})
    followup_gate: str = field(default="", metadata={"omit_empty": True})
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

    @property
    def family(self) -> str:
        """The event family this item's definition events are written under: `phase`, or
        `task` for every work kind (`task.added` carries the kind in its payload and the
        other `task.*` events take it from the item's definition, so a doc needs no `doc.*`
        events of its own)."""
        return "phase" if self.kind == "phase" else "task"

    @property
    def is_work(self) -> bool:
        """A unit of work with its own claim, worktree and gates: a task, or an item of a
        kind that runs like one (`WORK_KINDS`). A phase is a container."""
        return self.kind in WORK_KINDS

    @property
    def terminal(self) -> bool:
        """Finished with: done or abandoned. The one test for "nothing more to do here"."""
        return self.state in (DONE, ABANDONED)

    def runs_of(self, gate: str) -> list[GateRun]:
        """The gate's recorded outcomes, oldest first."""
        return [r for r in self.gate_history if r.gate == gate]

    def gate_outcome(self, gate: str) -> str:
        rec = self.gates.get(gate)
        return rec.outcome if rec else ""

    def gate_satisfied(self, gate: str, required: bool) -> bool:
        """Is nothing left to do for ``gate``? See `GateOutcome.satisfies`."""
        return GateOutcome.settled(self.gate_outcome(gate), required)

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

    def lease_taken_over_by(self) -> str:
        """Who FIRST took over the DISPLAYED claim, "" when nobody did (D-contest-redisplay).

        First, not latest: the displaced record keeps one entry per claim window
        (`handlers.leases._displace`), so a second takeover of the same claim adds none.

        A contested claim another holder took over stays in the contest, and when that
        takeover is released it is displayed again (`handlers.leases._redisplay`). It is
        live as far as the log can tell, but the person reading it must know it was once
        taken over: its holder may have moved on. Matched by claim window (event and
        start), so a claim `resolve` kept in a fresh window is not marked."""
        if self.lease is None:
            return ""
        for e in self.displaced:
            by = e.get("by", {}).get("holder", "")
            if (
                e["event"] == self.lease.event
                and e["lease"]["acquired_at"] == self.lease.acquired_at
                and by
                and by != self.lease.holder
            ):
                return by
        return ""

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


_LEASE_FIELDS = tuple(f.name for f in fields(Lease))


# Claim-window helpers: `State`'s contest methods use them, and so do the lease handlers.
def _claim(lease: Lease) -> dict[str, Any]:
    """The claim window of ``lease``: a copy, so widening it never moves the lease.

    Built from a flat read of the fields (the only mutable ones are two lists, copied):
    `dataclasses.asdict` recurses and deep-copies, and was 40% of a fold's time, called
    three times per renewal."""
    snap = {n: getattr(lease, n) for n in _LEASE_FIELDS}
    snap["globs"] = list(lease.globs)
    snap["resources"] = list(lease.resources)
    return {"holder": lease.holder, "event": lease.event, "lease": snap}


def _span(lease: dict[str, Any]) -> tuple[float, float]:
    """A claim's interval by its own evidence: acquired, to its last renewal plus TTL."""
    return lease["acquired_at"], lease["renewed_at"] + lease["ttl_s"]


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two claims' intervals intersect -- on BOTH ends. One-sided, a claim that ended
    before the other began read as its rival whenever it happened to fold second."""
    (a0, a1), (b0, b1) = _span(a), _span(b)
    return a0 <= b1 and b0 <= a1


def _other_hand(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two claims were made by different parties: different holders, or the SAME name
    from two clones that each recorded which clone they are (B36aa4c8ee5). A claim
    without a clone never counts as another clone of its own name."""
    if a["holder"] != b["holder"]:
        return True
    ca, cb = a["lease"].get("clone", ""), b["lease"].get("clone", "")
    return bool(ca and cb and ca != cb)


def _clashing(claims: list[dict[str, Any]], claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The claims among ``claims`` that overlapped ``claim``: another party's (see
    `_other_hand`), by both claims' own windows."""
    return [
        h
        for h in claims
        if _other_hand(h, claim)
        and h["event"] != claim["event"]
        and _overlaps(h["lease"], claim["lease"])
    ]
