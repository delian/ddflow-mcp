"""Dependency resolution and readiness.

The scheduler answers exactly one question: *given the current state, which items may
start RIGHT NOW, and for each item that may not, why not?* The "why not" half is not a
nicety — an agent told only "nothing to do" will invent work, whereas an agent told
"T3 waits on T2, which agent-b holds until 14:05" waits correctly or picks another
phase.

Two independent reasons block an item, and they are reported separately because the
remedies differ:

* **Dependency** — ``needs`` names an item that is not done. Remedy: wait, or do that.
* **Conflict** — another agent holds a live lease whose file globs overlap ours.
  Remedy: pick a non-overlapping item; the whole point of reporting the non-overlapping
  ones is that re-ordering beats blocking.

Glob overlap is deliberately over-eager. A false positive costs one unnecessary
re-order; a false negative costs two agents editing one file and one of them silently
losing their work. The asymmetry is not close, so the comparison errs toward "yes".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...config import Config
from ..autoorder import is_decide
from ..flow import stack_base, unknown_line
from ..globs import inside as path_in_glob
from ..graph import find_cycles
from ..model import ABANDONED, BLOCKED, DONE, REVIEW, Item, State


@dataclass
class Blocked:
    item: str
    reason: str  # "deps" | "conflict" | "state" | "cycle" | "umbrella" | "contested" | ...
    detail: str = ""
    waiting_on: list[str] = field(default_factory=list)


@dataclass
class Unpickable:
    """Work that EXISTS in the queue and that no `ddflow next` call can offer.

    B19, from the source project: 37 follow-ups — including four confirmed reviewer
    findings — were filed where the picker could not see them, and nothing failed. The
    counts reconciled and every audit exited 0, because each of them measured the items
    that were there rather than asking whether any of them could be picked up.
    """

    item: str
    kind: str  #: empty_phase | finished_phase
    detail: str
    severity: str  #: note | problem

    def render(self) -> str:
        return f"{self.item}: {self.detail}"


def unpickable(state: State, cfg: Config) -> list[Unpickable]:
    """Every open item that `next` will never offer, with why.

    **Tasks cannot appear here, and that is a property worth stating rather than a gap in
    the check.** `plan()` with no phase walks `state.tasks()`, which returns every live
    task, and puts each into exactly one of ready/running/blocked/review — so a filed task is
    always reachable. Two probes went looking for a task the picker could miss: a task
    parented to a phase id that does not exist is still reachable, because
    `descendants()` is built from the parent FIELD rather than from the items; and a task
    filed with a foreign `kind` is impossible, because `_h_added` takes the kind from the
    event kind and ignores `data`. Both hypotheses REFUTED, and
    `tests/test_pickability.py` pins the invariant so a future filter cannot quietly
    reintroduce it.

    What IS reachable is a PHASE nothing can be picked under, which `next` reports as an
    empty queue.
    """
    out: list[Unpickable] = []
    for ph in sorted(state.items.values(), key=lambda i: i.id):
        if ph.kind != "phase" or ph.removed or ph.terminal:
            continue
        tasks = [t for t in state.tasks(ph.id) if not t.removed]
        live = [t for t in tasks if not t.terminal]
        if live:
            continue
        if not tasks:
            if cfg.schedule.empty_phase == "off":
                continue
            out.append(
                Unpickable(
                    ph.id,
                    "empty_phase",
                    "an open phase with no task under it, so `next` has nothing to offer "
                    "for it — break it down, or remove it",
                    "problem" if cfg.schedule.empty_phase == "problem" else "note",
                )
            )
            continue
        # Tasks exist and every one is finished. Always a problem, and deliberately NOT
        # behind the knob: an open phase over finished work is not a workflow style, it is
        # a queue held open by an item nobody can act on, and the remedy (`complete` it,
        # or file what is left) is the same in every project. Whether `complete` would
        # succeed is NOT decided here: that is `services.completion.verdict()`, which core
        # may not import, so `doctor` replaces this remedy with the verdict's answer
        # (B5189cc5756 -- a re-derived rule here missed failed gates and advisory silence).
        out.append(
            Unpickable(
                ph.id,
                "finished_phase",
                f"all {len(tasks)} task(s) under it are finished but the phase is still "
                f"open — `ddflow complete {ph.id}`, or file the work that remains",
                "problem",
            )
        )
    return out


@dataclass
class Plan:
    """What the scheduler decided, and everything it decided against."""

    ready: list[Item] = field(default_factory=list)
    #: Ready work items OUTSIDE ``phase`` that ``phase`` transitively waits on, surfaced
    #: by `next --phase` as blocking-dependencies (never as in-phase items). Empty unless
    #: the caller asked for them (`plan(..., with_deps=True)`).
    blocking_deps: list[Item] = field(default_factory=list)
    blocked: list[Blocked] = field(default_factory=list)
    running: list[Item] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    #: Items the log says are RUNNING with nobody holding them — see `interrupted`.
    #: Offered as ready (someone must resume them) but never silently.
    interrupted: list[str] = field(default_factory=list)
    #: Waiting on a pull request. Not running (nobody holds it), not blocked (nothing is
    #: wrong), not ready (there is nothing for an agent to do until a reviewer acts).
    review: list[Item] = field(default_factory=list)
    #: Ids that nothing blocks but a parallelism cap (`schedule.max_parallel_tasks` or
    #: `worktree.max_parallel`): ready in every other sense, so a count of "ready" plus
    #: "blocked on something" that leaves them out does not add up (Bdcce70d036). Each
    #: is ALSO in `blocked` with reason "state", which `next`, `wait` and their callers
    #: read; `cap_note` is the one sentence that says which cap.
    capped: list[str] = field(default_factory=list)
    cap_note: str = ""
    #: Ids ready in every other sense that overlap an item offered in this same plan: each
    #: is ALSO in `blocked` with reason "conflict", and its slot went to an independent
    #: item. Not cap-held: raising the cap would not offer it, only the offered item
    #: finishing would.
    overlapped: list[str] = field(default_factory=list)
    #: The one line `status` and `brief` print about the parallelism limit -- "parallel: 6
    #: (auto: ceiling 8; limited by load per core)" or "parallel: 4 (fixed)" -- set only
    #: when the caller passed the limit in force (`plan(parallel=...)`).
    parallel_line: str = ""
    #: How many slots `schedule.reserved_slots` keeps and for what; "" when none.
    reserved_note: str = ""
    #: Open phases whose every task is finished (`unpickable`'s "finished_phase"), in
    #: scope of the plan's `phase`. Offered to CLOSE -- the phase's own pipeline, then
    #: `complete` -- and never closed here: its gates still decide (B28268eba1a).
    finished: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{len(self.ready)} ready",
            f"{len(self.running)} running",
            f"{len(self.blocked) - len(self.capped) - len(self.overlapped)} blocked",
        ]
        if self.capped:
            parts.append(f"{len(self.capped)} held by {self.cap_note}")
        if self.overlapped:
            parts.append(f"{len(self.overlapped)} overlap an offered item")
        if self.reserved_note:
            parts.append(self.reserved_note)
        if self.cycles:
            parts.append(f"{len(self.cycles)} CYCLE(S)")
        if self.interrupted:
            parts.append(f"{len(self.interrupted)} INTERRUPTED")
        if self.review:
            parts.append(f"{len(self.review)} in review")
        if self.finished:
            parts.append(f"{len(self.finished)} finished phase(s) to close")
        return ", ".join(parts)

    def close_note(self) -> str:
        """What to run for each finished phase, or "" -- one wording for `next` and the
        brief."""
        return "".join(
            f"\nPhase {ph} is finished but still open: run its pipeline "
            f"(`ddflow gate status {ph}`), then `ddflow complete {ph}`."
            for ph in self.finished
        )


def stale_package_globs(globs: Iterable[str], tracked: Iterable[str]) -> list[tuple[str, str]]:
    """(glob, correction) for each literal `x.py` glob that is not a tracked file while a
    package `x/` is: the module was split into a package, and a lease on the old name covers
    none of its files, so the conflict detector cannot see two tasks editing them
    (B56dc2baaf6). A glob matching nothing with no such package is a file the task will
    create, and is left alone."""
    files = list(tracked)
    out = []
    for g in globs:
        if not g.endswith(".py") or any(c in g for c in "*?[") or g in files:
            continue
        pkg = g[: -len(".py")] + "/"
        if any(f.startswith(pkg) for f in files):
            out.append((g, pkg))
    return out


def is_external(dep: str) -> bool:
    """`run_nemo_run:132.D` -- an item in a sibling repository (`[schedule] repos`).

    A colon cannot occur in a local id: every writer of ids here (the importer's id
    grammar, auto ids, the CLI) produces letters, digits, `.`, `_` and `-`.
    """
    repo, sep, item = dep.partition(":")
    return bool(sep and repo and item)


def dep_status(state: State, dep: str, cfg: Config) -> tuple[bool, str]:
    """Is one dependency satisfied? Returns (satisfied, explanation).

    An UNKNOWN id is unmet by default (``schedule.unknown_dep_policy``), so a typo in
    ``needs`` surfaces as a blocked item rather than as an item that silently starts
    early. Treating unknown as satisfied is the vacuous-truth trap in its purest form.
    """
    if is_external(dep) and dep not in state.items:
        seen = state.external.get(dep)
        if seen is None:
            return False, (
                f"{dep} is in another repository and has not been observed yet: "
                f"`ddflow external sync`"
            )
        if seen["state"] == DONE:
            return True, ""
        return False, f"{dep} is {seen['state'] or 'missing'} (observed {seen['at'][:16]})"
    it = state.items.get(dep)
    if it is None or it.removed:
        if cfg.schedule.unknown_dep_policy == "block":
            return False, f"unknown dependency {dep!r} (typo? or not yet added)"
        return True, f"unknown dependency {dep!r} ignored by policy"
    if it.terminal:  # settled: done, or abandoned (decided NOT done)
        # An abandoned need is settled too: nothing will ever finish it, so a dependent
        # must not wait on it forever (B740b7f8cf4). An abandoned phase with orphaned
        # sub-tasks satisfies the same way: its dependents are not the place to resolve
        # those orphans.
        note = {ABANDONED: f"{dep} was abandoned; its dependents are not blocked by it"}.get(
            it.state, ""
        )
        return True, note
    if it.state == REVIEW:
        # Satisfied only for STACKING, which `plan_blocker` then checks is possible --
        # the dependent forks from this branch. Without stacking, work waits for the merge.
        where = f" ({it.pr.url})" if it.pr and it.pr.url else ""
        if cfg.flow.stack and it.branch:
            return True, f"{dep} is in review{where}; stacking on {it.branch}"
        return False, f"{dep} is awaiting review{where}"
    if it.kind == "phase":
        kids = [t for t in state.tasks(it.id) if t.state != DONE]
        if not kids and it.state != DONE:
            return False, f"phase {dep} has no open tasks but is not marked done"
        return False, f"phase {dep} has {len(kids)} open task(s)"
    return False, f"{dep} is {it.state}"


def _docs_only_exempt(it: Item, cfg: Config | None) -> bool:
    """A research/doc task (or any task) whose declared globs are ALL documents writes no
    code, so the phase's gates on other phases' code do not hold it back. A research or doc
    task with no globs is exempt too. `[schedule].docs_ignore_phase_needs` turns it off;
    `[gates].doc_globs` says what a document is. Its own `needs` still bind."""
    cfg = cfg if cfg is not None else Config()
    if not cfg.schedule.docs_ignore_phase_needs or not it.is_work:
        return False
    if not it.globs:
        return it.kind in ("research", "doc")
    docs = cfg.gates.doc_globs
    return bool(docs) and all(_glob_is_docs(g, docs) for g in it.globs)


def _glob_is_docs(glob: str, docs: list[str]) -> bool:
    """Is every file ``glob`` can match a document? Decided by containment rules that
    cannot over-claim, never by sampling: the glob IS a doc glob; a doc glob `dir/**`
    holds everything under `dir/`; a doc glob `**/*.ext` (or `*.ext`) holds every path
    whose last component ends `.ext`; and a doc glob `dir/*` holds a single-level glob under `dir/`; a glob with no wildcard is one path to test."""
    if ".." in glob.split("/"):
        return False
    if glob in docs:
        return True
    last = glob.rsplit("/", 1)[-1]
    for d in docs:
        if d.endswith("/**") and glob.startswith(d[:-2]):
            return True
        if d.endswith("/*") and not d.endswith("**") and glob.startswith(d[:-1]):
            if "/" not in glob[len(d) - 1 :] and "**" not in glob:
                return True
        tail = d[len("**/") :] if d.startswith("**/") else d
        if (
            tail.startswith("*")
            and not any(c in tail[1:] for c in "*?[/")
            and last.endswith(tail[1:])
        ):
            return True
    return not any(c in glob for c in "*?[") and any(path_in_glob(glob, d) for d in docs)


def inherited_deps(state: State, it: Item, cfg: Config | None = None) -> list[tuple[str, str]]:
    """Every dependency that binds ``it``: its own, plus every ancestor's.

    Returns ``(owner_id, dep_id)`` pairs so a refusal can say WHERE the dependency
    came from — told only "P2.T1 needs P1", an operator goes looking for a
    declaration that is not in P2.T1 and concludes the tool is confused.

    **Why inheritance is the whole point.** A phase is never claimed; only its tasks
    are. So a readiness rule reading ``it.needs`` and stopping there makes every
    phase-level dependency decorative: ``P2 needs P1`` blocks P2, which no agent was
    going to pick up, and permits every task inside P2, which is what an agent
    actually starts. The same hole appeared one level down once sub-tasks existed —
    an umbrella declaring ``needs A, B`` had children with empty ``needs``, so they
    were handed out while A and B were still open.

    The one dependency that must NOT be inherited is one pointing into ``it``'s own
    subtree. An umbrella that declares a dependency on its own child would otherwise
    make that child wait for itself, and a plan typo would become a permanent hang.
    Beneath an umbrella, such a dependency is satisfied by running, not by waiting;
    the umbrella still carries it, and the umbrella cannot close early anyway.

    Nor is one pointing at one of ``it``'s own ancestors: A needs its child C, and C's
    sub-task D inherited "C is open" while C, an umbrella, waited on D (B43447abfc8).
    An ancestor closes only after ``it`` does, so waiting on it is the same hang.
    """
    pairs: list[tuple[str, str]] = [(it.id, d) for d in it.needs]
    ancestors = state.ancestors(it.id)
    # A decide task is the operator's first move and writes only a document, so a phase's
    # gates ("8 open tasks inherited from P-x") never hold it back; its own `needs` still do.
    if not ancestors or is_decide(it.tags) or _docs_only_exempt(it, cfg):
        return pairs
    mine = state.descendants(it.id) | {it.id} | {a.id for a in ancestors}
    for anc in ancestors:
        pairs += [(anc.id, d) for d in anc.needs if d not in mine]
    seen: set[tuple[str, str]] = set()
    return [p for p in pairs if not (p in seen or seen.add(p))]


def _is_umbrella(state: State, it: Item) -> bool:
    """Does this item have work beneath it?

    An umbrella is not something to claim — its children are. Offering it would give an
    agent a worktree for an item whose actual work lives in three other items, and the
    umbrella cannot complete until they do anyway.
    """
    return bool(state.open_descendants(it.id))


def _settled_umbrella_detail(state: State, it: Item) -> str:
    """Why a task whose sub-tasks are ALL settled is not ready work, or "" when it is not
    one -- nor for a task that is itself done or abandoned. Self-contained (it does not lean
    on the open-umbrella check before it).

    With at least one sub-task done, the work is finished and `complete` records it
    (`api.lifecycle.complete._umbrella_children` is the same rule on the api side; core
    cannot import it). With every one abandoned there is nothing to complete: it is the
    operator's call to abandon it too or give it work."""
    if not it.is_work or it.terminal:
        return ""
    below = state.descendants(it.id)
    if not below or state.open_descendants(it.id):
        return ""
    if any(state.items[i].state == DONE for i in below):
        return (
            f"its sub-tasks are all settled: `ddflow complete {it.id}` records it with them "
            "(an open bug it was filed to fix still needs its --regression-test)."
        )
    return (
        f"every sub-task was abandoned: abandon it too (`ddflow abandon {it.id} --reason ...`) "
        "or give it work."
    )


def plan_blocker(
    state: State,
    cfg: Config,
    it: Item,
    *,
    in_cycle: set[str] | None = None,
    cycles: list[list[str]] | None = None,
) -> Blocked | None:
    """Why the PLAN forbids starting this item — umbrella, cycle or dependency.

    Split out from :func:`item_blocker` because two layers need exactly this much and
    no more. The scheduler adds the lease/conflict questions on top; the lease layer
    asks them itself, unconditionally, because refusing an overlapping claim is a
    safety property rather than a scheduling preference and must not be switchable
    off by ``schedule.ready_policy``.

    Sharing it is not tidiness. ``claim`` used to check leases and globs but *no*
    dependencies at all, so `next` would report "T2: deps — T1 is open" and `claim T2`
    would hand out a worktree one second later. An agent that picks work by id rather
    than by asking `next` therefore bypassed the entire dependency graph — the one
    guarantee the queue exists to provide.
    """
    if in_cycle is None or cycles is None:
        cycles = find_cycles(state.live_by_id())
        in_cycle = {n for c in cycles for n in c}
    contest = it.contest_summary()
    if contest:
        # B191: two clones disagree about what this item is, or who holds it. Whichever
        # the fold happens to display, starting it would build on one side of a
        # disagreement nobody has settled.
        return Blocked(
            it.id,
            "contested",
            f"{contest}. `ddflow resolve {it.id} --keep <event-id|agent>` settles it.",
            [],
        )
    if it.state == BLOCKED:
        return Blocked(
            it.id,
            "state",
            it.blocked_reason or "parked by an operator",
            [],
        )
    if _is_umbrella(state, it):
        kids = state.open_descendants(it.id)
        return Blocked(
            it.id,
            "umbrella",
            f"has {len(kids)} unfinished sub-task(s): "
            f"{', '.join(k.id for k in kids[:6])}. Work those; this closes when they do.",
            [k.id for k in kids],
        )
    settled = _settled_umbrella_detail(state, it)
    if settled:
        # Settled before its last sub-task's completion closed it (B797aff72d8): there is
        # nothing to claim, only a decision to record.
        return Blocked(it.id, "umbrella", settled, [])
    if it.id in in_cycle and cfg.schedule.cycle_policy == "error":
        cyc = next(c for c in cycles if it.id in c)
        return Blocked(it.id, "cycle", " -> ".join(cyc), [])

    unmet: list[str] = []
    details: list[str] = []
    for owner, dep in inherited_deps(state, it, cfg):
        ok, why = dep_status(state, dep, cfg)
        if not ok:
            unmet.append(dep)
            details.append(why if owner == it.id else f"{why} (inherited from {owner})")
    if unmet:
        return Blocked(it.id, "deps", "; ".join(details), unmet)
    gone = unknown_line(state, it, cfg)
    if gone:
        return Blocked(
            it.id,
            "state",
            f"its release line {gone!r} is not in [flow.lines] any more. Restore the line, "
            f"or `ddflow update {it.id} --line <line>` deliberately.",
            [],
        )
    src = state.items.get(it.port_from) if it.port_from else None
    if src is not None and src.state != DONE:
        # `needs` is satisfied by REVIEW when stacking; a port is not. It applies what its
        # source LANDED, and nothing has landed until the request merges.
        return Blocked(
            it.id,
            "deps",
            f"a port of {it.port_of}: waits for {src.id} to land ({src.state})",
            [src.id],
        )
    stack = stack_base(state, it, cfg, [d for _, d in inherited_deps(state, it, cfg)])
    if stack.error:
        return Blocked(it.id, "deps", stack.error, [])
    return None
