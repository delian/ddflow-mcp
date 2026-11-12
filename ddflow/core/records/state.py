"""`State` — the folded whole — and `record_pools`.

Part of the former `ddflow/core/records.py`, split into submodules that stay under
30k chars each (rule r-small-files). `model.fold` builds a `State` by running
`model.HANDLERS` (`core/handlers/`) over the events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..defs import DefRecord
from ..events import version_key
from ..graph import closure
from .content import (
    Bug,
    Decision,
    FoldProblem,
    Job,
    Lesson,
    Memory,
    RecordLinks,
    Release,
    ResearchNote,
    Schedule,
    Session,
)
from .items import Item, Lease


def record_pools(st: State) -> tuple[tuple[str, dict[str, Any]], ...]:
    """`(kind, pool)` for the records two agents can define under one id and so contest:
    decisions, lessons and research notes. The one list `doctor`, `resolve` and the upgrade
    plan walk, so a fourth kind is added here and nowhere else."""
    return (("decision", st.decisions), ("lesson", st.lessons), ("research", st.research))


@dataclass
class State:
    #: `ci.result`: the last CI_RESULTS_KEPT outcomes of the CI command, oldest first.
    ci_results: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, Item] = field(default_factory=dict)
    #: ids whose LATEST completion was an import's (`item.completed` with `imported`):
    #: finished before ddflow existed, so no count-based cadence pass measures them.
    imported_done: set[str] = field(default_factory=set)
    bugs: dict[str, Bug] = field(default_factory=dict)
    lessons: dict[str, Lesson] = field(default_factory=dict)
    research: dict[str, ResearchNote] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    memories: dict[str, Memory] = field(default_factory=dict)
    #: `memory.recorded` event ids whose superseded text was redacted from the log
    #: (`memory.redacted`): the auditable record that the text no longer stands.
    redacted_memory_events: set[str] = field(default_factory=set)
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
    #: `kind:id` -> the definition that currently stands for a decision, lesson or research
    #: record, in the same shape: what a rival from another agent is compared against.
    record_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: reviewer-entry digest -> who a TOOL wrote it as ({"name", "kind", "agent",
    #: "person", "user", "at"}), and digest -> the person who approved it. A digest in
    #: the first and not the second is a reviewer whose reviews do not count yet
    #: (decision D-reviewer-trust). An entry no tool wrote appears in neither.
    reviewer_writes: dict[str, dict[str, Any]] = field(default_factory=dict)
    reviewer_approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: subject -> every approval a person gave it, oldest first (`services.approval`):
    #: {"digest", "user", "host", "human", "note", "token_hash", "used_at", "agent", "at"}.
    #: What runs is approved only when its digest is one approved here.
    approvals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: ask id -> the folded ask (`ask.*` events, folded by `core.handlers.asks`; read it
    #: through `asks_of`, never directly). Declared here (SE-state-asks-field) instead of
    #: setdefault'd beside the declared fields: the ask rows belong to the record shape,
    #: so field-walking state users see them, and an instance restored without `__init__`
    #: (a pre-field pickle) still gets the dict lazily from `asks_of`. The rows hold
    #: person-supplied text, so any full-state export redacts it leaf-wise like every
    #: other field.
    asks: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: token id -> who holds a paired token (`token.*` events, `core.handlers.tokens`; read it
    #: through `tokens_of`). Name, role and dates only: no event carries a secret or a hash.
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: suggestion id -> the folded suggestion (`suggestion.*` events, `core.handlers.suggestions`;
    #: read it through `suggestions_of`). Holds the patch's digest and counts, never its text.
    suggestions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: item id -> the newest `lease.waiting` record of an agent refused the files it asked for
    #: ({"holder", "globs", "on", "since", "queue", "ttl_s", "lease_event", "agent", "at"}).
    #: Whether it still stands is asked at read time (`services.waiting`), never in the fold.
    waiting: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: agent -> "path\0key" -> the newest `agent.onboarded` record of that artifact ({"path",
    #: "kind", "key", "digest", "descriptor", "version"}); read it through `onboarded_of`.
    onboarded: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: agent -> its newest `agent.smoke` record ({"verdict", "reason", "attempts"}); read it through `smoke_of`.
    smoke: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: document kind -> who last enabled or disabled its export and when, and whether the
    #: operator vetoed it: {"enabled", "by", "human", "at", "path", "mode", "local",
    #: "locked", "acked"}. The SELECTION itself lives in the config; this is the record
    #: of who changed it (decision D-export-agent-enable).
    exports: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: record id -> its links and additions. Only records that have any appear.
    links: dict[str, RecordLinks] = field(default_factory=dict)
    #: "kind:id" -> a managed definition (`core.defs`): doc types, schedules, triggers,
    #: skills, agents, research claims and rules, from the `def.*` events.
    defs: dict[str, DefRecord] = field(default_factory=dict)
    last_lamport: int = 0
    event_count: int = 0
    #: kind -> count, for events a non-strict fold could not interpret. Counted rather
    #: than ignored so a caller can refuse to act on a partially-understood log.
    skipped_kinds: dict[str, int] = field(default_factory=dict)
    #: Events a non-strict fold could not apply, in fold order (`FoldProblem`). A strict
    #: fold raises instead.
    fold_problems: list[FoldProblem] = field(default_factory=list)
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
    #: Upgrade RUNS (`upgrade.applied` with a `run` id), by run id in start order: {"from", "to",
    #: "categories", "items" (planned), "steps" ([{"id", "status", "detail", "at"}]), "closed"
    #: ("" while open, else "done" or "failed"), "started", "agent", "starts"}. A run that is
    #: still open was interrupted; `ddflow upgrade --resume` finishes it.
    upgrade_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: repair id -> each application of that versioned data repair (`repair.applied`),
    #: oldest first: {"findings" (the keys it settled), "since", "version", "events",
    #: "reason", "note", "by" (an acceptance's), "agent", "at"}. `services.repairs` never offers a finding whose key is listed here.
    repairs_applied: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: version -> how many events were written by that OLDER ddflow under a skew override
    #: (the `older_ddflow` mark on their data).
    older_version_events: dict[str, int] = field(default_factory=dict)

    #: Capability name -> what the log records of it (`ddflow.capabilities`): {"kinds" (sorted),
    #: "version" (the highest release providing it), "agents", "at"}.
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        if not phase:
            return [i for i in self.items.values() if i.is_work and not i.removed]
        # Only the items below ``phase``, put back in definition order: scanning every item
        # for each of the thousand phase dependencies a plan weighs was the cost of `next`.
        pos = self._positions()
        below = [self.items[n] for n in self.descendants(phase) if n in self.items and n in pos]
        below.sort(key=lambda i: pos[i.id])
        return [i for i in below if i.is_work and not i.removed]

    def _positions(self) -> dict[str, int]:
        """`item id -> definition order`. Items are only ever added (a removal sets a flag), so
        a table of this length is still right."""
        table = self.__dict__.get("_pos_table")
        if table is None or len(table) != len(self.items):
            table = {n: k for k, n in enumerate(self.items)}
            self.__dict__["_pos_table"] = table
        return table

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
        return set(closure(item_id, lambda n: [c.id for c in self.children(n)]))

    def ancestors(self, item_id: str) -> list[Item]:
        """The parent chain above ``item_id``, nearest first.

        Cycle-guarded for the same reason ``descendants`` is: the chain is
        operator-authored, so a mistake can make it circular, and the code that walks
        it is the code that diagnoses bad plans.
        """

        def parent(n: str) -> list[str]:
            node = self.items.get(n)
            up = self.items.get(node.parent) if node is not None and node.parent else None
            return [up.id] if up is not None and not up.removed else []

        return [self.items[n] for n in closure(item_id, parent) if n != item_id]

    def live_items(self) -> list[Item]:
        """Every item that has not been removed, in definition order."""
        return [i for i in self.items.values() if not i.removed]

    def live_by_id(self) -> dict[str, Item]:
        """`live_items` keyed by id: what the graph walks (`find_cycles`) take."""
        return {i.id: i for i in self.live_items()}

    def open_descendants(self, item_id: str) -> list[Item]:
        """Descendants that are neither done nor abandoned — what blocks completion."""
        return [
            self.items[i] for i in sorted(self.descendants(item_id)) if not self.items[i].terminal
        ]

    def active_leases(self, now: float, grace_s: int = 0) -> dict[str, Lease]:
        return {
            i.id: i.lease for i in self.items.values() if i.lease and i.lease.live(now, grace_s)
        }

    def expired_leases(self, now: float, grace_s: int = 0) -> dict[str, Lease]:
        return {
            i.id: i.lease for i in self.items.values() if i.lease and not i.lease.live(now, grace_s)
        }
