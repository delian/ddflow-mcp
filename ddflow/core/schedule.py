"""Dependency resolution, readiness and parallel fan-out.

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

import functools
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch

from ..config import Config
from ..core.model import ABANDONED, BLOCKED, DONE, REVIEW, RUNNING, Item, Lease, State
from . import flowcontrol as FC
from .flow import FEATURE, branch_kind, line_key, stack_base, unknown_line


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
        if ph.kind != "phase" or ph.removed or ph.state in (DONE, ABANDONED):
            continue
        tasks = [t for t in state.tasks(ph.id) if not t.removed]
        live = [t for t in tasks if t.state not in (DONE, ABANDONED)]
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


def globs_overlap(a: str, b: str) -> bool:
    """Do two path patterns plausibly cover a common file?

    Exact match, either pattern matching the other as a literal, or a shared literal
    prefix. Deliberately approximate: computing true regular-language intersection of
    two globs is both hard and beside the point, since the answer only decides whether
    to re-order.
    """
    if a == b:
        return True
    if fnmatch(a, b) or fnmatch(b, a):
        return True
    pa = a.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    pb = b.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    if not pa or not pb:
        return True  # a bare "*" covers everything
    return pa.startswith(pb) or pb.startswith(pa)


def shared_globs(cfg: Config) -> list[str]:
    """`[lease] shared_globs` and `append_only_globs`: paths many items may hold at once
    (D-shared-globs), plus every SELECTED export target (D-export (2)): a generated document
    is regenerated by whoever runs `ddflow export`, so no claim on it is needed. A `region`
    target is a hand-written file with a generated region; it is shared for the same reason."""
    lease = cfg.lease
    return [
        *lease.shared_globs,
        *lease.append_only_globs,
        *(p for _d, p, _m in cfg.export.targets()),
    ]


def _class_end(pat: str, i: int) -> int:
    """Index of the `]` closing the class opened at ``pat[i]``, or -1 (then `[` is literal).

    A `]` right after `[`, `[!` or `[^` is a MEMBER, as in git and Python: `[]]`, `[^]]`.
    Taken as the closer, it left `[^]` -- an invalid regex that crashed a claim.
    """
    k = i + 1
    if k < len(pat) and pat[k] in "!^":
        k += 1
    if k < len(pat) and pat[k] == "]":
        k += 1
    return pat.find("]", k)


@functools.lru_cache(maxsize=256)
def _gitattributes_re(pattern: str) -> re.Pattern[str]:
    """``pattern`` as git matches it in `.gitattributes` (gitignore rules).

    `*` and `?` stop at `/`; `**/` is any leading directories (none included), `/**` and
    `**` anything below; a pattern with no `/` matches the name at any depth, one with a
    `/` is anchored at the root. fnmatch's `*` crosses `/` and its `**/x` needs a `/`, so
    it disagreed with the very line `merge=union` is written as (review finding).
    """
    anchored = "/" in pattern.rstrip("/")
    pat = pattern.lstrip("/")
    out: list[str] = []
    i = 0
    while i < len(pat):
        # `**` is "any depth" only on a path boundary -- a leading `**/`, a `/**/`, a
        # trailing `/**`; elsewhere it is two plain `*`s, which stop at `/` (gitignore(5)).
        at_start = i == 0 or pat[i - 1] == "/"
        if at_start and pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif at_start and pat.startswith("**", i) and i + 2 == len(pat):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[" and _class_end(pat, i) != -1:
            j = _class_end(pat, i)
            body = pat[i + 1 : j].replace("\\", "\\\\")
            # git negates with `[!...]` as well as `[^...]`; Python knows only `^`.
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = j + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + r"\Z")


def is_shared(glob: str, shared: list[str]) -> bool:
    """Is ``glob`` (a claim's glob or a staged path) INSIDE one of the ``shared`` globs?

    Inside, not overlapping: `docs/**` merely overlaps a shared `docs/CHANGELOG.md` and
    still claims the rest of `docs/`, so it stays exclusive. Matched as git matches the
    `.gitattributes` line (`_gitattributes_re`), so "shared" and "merged with union" agree.
    """
    for s in shared:
        try:
            if glob == s or _gitattributes_re(s).match(glob):
                return True
        except re.error:
            continue  # a pattern git would read and we cannot: equality only, never a crash
    return False


def conflicts(
    mine: list[str], theirs: list[str], shared: list[str] | None = None
) -> list[tuple[str, str]]:
    """Overlapping pairs between two glob lists; a glob inside ``shared`` overlaps nothing.

    Every parallel item edits a changelog; exclusive leases on it serialised them all
    or pushed agents to commit it unleased (B07878037ab).
    """
    shared = shared or []
    mine = [a for a in mine if not is_shared(a, shared)]
    theirs = [b for b in theirs if not is_shared(b, shared)]
    return [(a, b) for a in mine for b in theirs if globs_overlap(a, b)]


def find_cycles(
    items: dict[str, Item], edges: Callable[[Item], list[str]] | None = None
) -> list[list[str]]:
    """Every dependency cycle, each reported once, starting at its smallest id.

    Iterative DFS. Recursion depth here is the length of the dependency chain, which is
    operator-authored and therefore unbounded by anything the code controls; a plan with
    a thousand-deep chain would raise RecursionError inside the health check that exists
    to diagnose bad plans.

    `edges` says which graph to walk, and a caller must pass the one it will walk
    itself. `critical_path` traverses INHERITED dependencies while this defaulted to
    direct `needs`, so a cycle existing only in the inherited graph passed the guard and
    the memoised longest-path returned a confidently wrong number -- the exact outcome
    the guard exists to refuse.
    """
    edge = edges or (lambda it: list(it.needs))
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    found: list[list[str]] = []

    for root in sorted(items):
        if colour.get(root, WHITE) != WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(root, edge(items[root]))]
        path: list[str] = [root]
        colour[root] = GREY
        while stack:
            _node, pending = stack[-1]
            if not pending:
                stack.pop()
                colour[path.pop()] = BLACK
                continue
            dep = pending.pop()
            if dep not in items:
                continue
            c = colour.get(dep, WHITE)
            if c == GREY:
                cyc = path[path.index(dep) :]
                lo = cyc.index(min(cyc))
                found.append(cyc[lo:] + cyc[:lo] + [cyc[lo]])
            elif c == WHITE:
                colour[dep] = GREY
                path.append(dep)
                stack.append((dep, edge(items[dep])))
    uniq = {tuple(c): c for c in found}
    return sorted(uniq.values())


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
    if it.state == DONE:
        return True, ""
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


def inherited_deps(state: State, it: Item) -> list[tuple[str, str]]:
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
    """
    pairs: list[tuple[str, str]] = [(it.id, d) for d in it.needs]
    ancestors = state.ancestors(it.id)
    if not ancestors:
        return pairs
    mine = state.descendants(it.id) | {it.id}
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
        cycles = find_cycles({i.id: i for i in state.items.values() if not i.removed})
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
    if it.id in in_cycle and cfg.schedule.cycle_policy == "error":
        cyc = next(c for c in cycles if it.id in c)
        return Blocked(it.id, "cycle", " -> ".join(cyc), [])

    unmet: list[str] = []
    details: list[str] = []
    for owner, dep in inherited_deps(state, it):
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
    stack = stack_base(state, it, cfg, [d for _, d in inherited_deps(state, it)])
    if stack.error:
        return Blocked(it.id, "deps", stack.error, [])
    return None


def item_blocker(
    state: State,
    cfg: Config,
    it: Item,
    live: dict[str, Lease],
    *,
    agent: str,
    now: float,
    in_cycle: set[str],
    cycles: list[list[str]],
) -> Blocked | None:
    """Why can this item not start? ``None`` means it can.

    The single readiness predicate for the whole package. It exists because three
    places once decided this independently and the copies disagreed: an agent refused
    one item was offered an alternative the scheduler would also refuse.
    """
    blocked = plan_blocker(state, cfg, it, in_cycle=in_cycle, cycles=cycles)
    if blocked is not None:
        return blocked

    if cfg.schedule.ready_policy != "deps_and_lease":
        return None

    held = live.get(it.id)
    if held and held.holder != agent:
        return Blocked(
            it.id,
            "conflict",
            f"leased by {held.holder} for another {held.remaining_s(now):.0f}s",
            [it.id],
        )
    mine = line_key(state, it, cfg)
    for other_id, lease in live.items():
        if other_id == it.id or lease.holder == agent:
            continue
        other = state.items.get(other_id)
        if other is not None and line_key(state, other, cfg) != mine:
            # Different release lines are different branches: `src/x.py` on 2.x and on
            # 3.x cannot collide, and refusing it would serialise every port behind its fix.
            continue
        pairs = conflicts(it.globs, lease.globs, shared_globs(cfg))
        if pairs:
            return Blocked(
                it.id,
                "conflict",
                f"globs overlap {other_id} held by {lease.holder} ({pairs[0][0]} vs {pairs[0][1]})",
                [other_id],
            )
    if it.resources and it.id not in live:
        try:
            short = resource_shortfall(it.resources, live, capacities(cfg), exclude=it.id)
        except ValueError as exc:
            short = str(exc)
        if short:
            return Blocked(it.id, "resources", short, [])
    return None


def parse_resources(specs: list[str]) -> dict[str, int]:
    """`["gpu:4", "vllm-fleet"]` -> `{"gpu": 4, "vllm-fleet": 1}`. Repeats add up.

    A count that is not a positive integer is a ValueError: silently reading `gpu:four`
    as 1 would let a claim through that the operator meant to be large.
    """
    out: dict[str, int] = {}
    for spec in specs:
        name, _, count = spec.strip().partition(":")
        name = name.strip()
        if not name:
            continue
        n = int(count) if count.strip() else 1
        if n < 1:
            raise ValueError(f"resource {spec!r}: the count must be a positive integer")
        out[name] = out.get(name, 0) + n
    return out


def capacities(cfg: Config) -> dict[str, int]:
    """`[schedule] resources` as a dict. Undeclared resources are exclusive (capacity 1).

    Raises ValueError naming the knob for an entry that is not `name=integer`: written
    in the ITEM syntax (`gpu:8`) it became a resource literally named "gpu:8" and left
    `gpu` at capacity 1, and `mem=64GB` raised an int() error that the claim then
    reported as a resource conflict (roborev 828).
    """
    out: dict[str, int] = {}
    for spec in cfg.schedule.resources:
        name, sep, cap = spec.partition("=")
        name, cap = name.strip(), cap.strip()
        if not sep or not name or ":" in name or not cap.isdigit() or int(cap) < 1:
            raise ValueError(
                f"[schedule] resources entry {spec!r} is not `name=capacity` with a "
                f'positive integer capacity (e.g. "gpu=8")'
            )
        out[name] = int(cap)
    return out


def resource_shortfall(
    want: list[str], live: dict[str, Lease], caps: dict[str, int], exclude: str = ""
) -> str:
    """Why `want` does not fit beside the live leases, or "" when it does.

    Every live lease counts, the caller's own included: resources are PHYSICAL. Two
    items held by one agent still want two sets of GPUs, unlike two items writing one
    file, where one author cannot collide with itself.
    """
    need = parse_resources(want)
    if not need:
        return ""
    used: dict[str, list[tuple[str, int]]] = {}
    for other_id, lease in live.items():
        if other_id == exclude:
            continue
        for name, n in parse_resources(lease.resources).items():
            used.setdefault(name, []).append((other_id, n))
    for name, n in need.items():
        cap = caps.get(name, 1)
        taken = sum(k for _i, k in used.get(name, []))
        if n > cap:
            return f"needs {n} {name} but the capacity is {cap} ([schedule] resources)"
        if taken + n > cap:
            holders = ", ".join(f"{i} ({k})" for i, k in used[name])
            return f"needs {n} {name}; {taken} of {cap} in use by {holders}"
    return ""


def interrupted(state: State, it: Item, live: dict[str, Lease]) -> str:
    """Why this item looks mid-flight with nobody on it. ``""`` when it does not.

    RUNNING with no live lease means the agent died between starting and claiming, or
    released without finishing. `lease.scan` has always called that `stale_running` and
    told an operator to recover it; `plan()` handed the same item to the next agent as
    ordinary ready work, with no mention that someone had been there. Both behaviours
    are defensible. Deciding them in two modules that never consult each other is not —
    the second agent starts from scratch on work that may exist, uncommitted, in a
    worktree nobody mentioned.

    So the classification lives here, once, and both callers ask it. The scheduler does
    NOT refuse the item — someone has to resume it, and refusing would strand it — it
    annotates the offer. `lease.scan` measures the worktree, which is I/O and stays in
    the service layer; this says only what the log says.
    """
    if it.state != RUNNING or it.id in live:
        return ""
    where = f" in {it.worktree}" if it.worktree else ""
    return (
        f"{it.id} is RUNNING with no live lease{where}: an agent started it and did not "
        f"finish. Run `ddflow recover --item {it.id}` before starting from scratch — "
        f"its worktree may hold uncommitted work."
    )


def bug_items(state: State, cfg: Config) -> set[str]:
    """Ids of the items that fix a bug, which `[schedule] bugs_first` offers first.

    A bug fix is what `branch_kind` already calls one -- a `bugfix_tags` or `hotfix_tags`
    tag, the same test gitflow names its branches by -- or an item an OPEN bug record
    names, or the fix task `bug found` filed for it (`fix_task`). A closed record stops
    counting -- fixed, or invalid (a false finding was never a bug): its item is ordinary
    work again.
    """
    ids = {i.id for i in state.items.values() if branch_kind(i, cfg) != FEATURE}
    for b in state.bugs.values():
        if b.open:
            ids.update(x for x in (b.item, b.fix_task) if x)
    return ids


def _offered_overlap(
    state: State, cfg: Config, it: Item, taken: list[Item], shared: list[str]
) -> tuple[str, tuple[str, str]] | None:
    """The first already-offered item whose globs overlap ``it``'s, with the pair; None
    when none does. Items on different release lines are different branches and never
    collide (the same exemption `item_blocker` makes for a held lease)."""
    mine = line_key(state, it, cfg)
    for other in taken:
        if line_key(state, other, cfg) != mine:
            continue
        pairs = conflicts(it.globs, other.globs, shared)
        if pairs:
            return other.id, pairs[0]
    return None


NO_TREE_TAG = "no-worktree"


def needs_tree(it: Item) -> bool:
    """False for an item that declares it takes no worktree (`no-worktree` tag): a review,
    a research task. Such an item counts against `schedule.max_parallel_tasks` only."""
    return NO_TREE_TAG not in (t.strip().lower() for t in it.tags)


def _cut_ready(
    state: State,
    cfg: Config,
    p: Plan,
    slots: int,
    flight_slots: int,
    tree_slots: int,
    live_items: list[str],
    live_note: str,
    reached: str,
    hold: Callable[[Item], Blocked | None] | None,
) -> None:
    """Trim ``p.ready`` to the free slots, in place, never offering two overlapping items."""
    # Cut the ready list conflict-aware: walk it in offer order and take an item only when
    # it overlaps nothing already taken (two offered items that overlap make the second
    # claim refuse). An overlapping item is not dropped -- it stays queued, blocked with
    # the offered item it waits behind.
    taken: list[Item] = []
    cut: list[Item] = []
    trees = 0  # taken items that will make a worktree
    shared = shared_globs(cfg)
    for it in p.ready:
        held = hold(it) if hold else None
        if held is not None:
            p.blocked.append(held)
            continue
        clash = _offered_overlap(state, cfg, it, taken, shared)
        if clash:
            other, pair = clash
            p.blocked.append(
                Blocked(
                    it.id,
                    "conflict",
                    f"globs overlap {other} ({pair[0]} vs {pair[1]}) offered in this plan",
                    [other],
                )
            )
            p.overlapped.append(it.id)
        elif len(taken) < flight_slots and (not needs_tree(it) or trees < tree_slots):
            taken.append(it)
            trees += needs_tree(it)
        else:
            cut.append(it)
    if cut:
        if slots:
            # NOT "cap reached": a slot is free, and a higher-ranked item is offered it.
            # Said as "reached", an agent waiting for the message to clear waited hours
            # on an item `claim` would have granted at once (B40386f7a40).
            names = ", ".join(i.id for i in taken)
            why = (
                f"held by {p.cap_note}: {live_note}; {slots} slot(s) free, offered to "
                f"higher-ranked {names}"
            )
        else:
            # `_blocking_leases` (api.lifecycle) keys on "cap reached".
            why = f"{reached}; {live_note}"
        # Any release frees a slot, so a cap-held item waits on every item in flight --
        # named here, so that a waiter registers against all of them whatever the wording
        # (the free-slot wording no longer says "cap reached").
        holders = sorted(live_items)
        for it in cut:
            p.blocked.append(Blocked(it.id, "state", why, holders))
            p.capped.append(it.id)
    p.ready = taken


def plan(
    state: State,
    cfg: Config,
    *,
    kind: str = "task",
    phase: str = "",
    now: float | None = None,
    agent: str = "",
    hold: Callable[[Item], Blocked | None] | None = None,
    parallel: FC.Decision | None = None,
) -> Plan:
    """Compute the ready set.

    ``hold`` lets a caller withhold an item the pure state cannot see is spoken for (a
    waiter in line, `claim` would refuse it): a Blocked it returns goes to ``blocked`` and
    the item neither takes a slot nor counts against the offered items' overlap check.

    ``phase`` restricts to one phase's tasks -- the "implement phase X" entry point.
    ``agent`` is the caller's identity: an item this agent already holds counts as
    running-by-me, not as a conflict against me.
    """
    now = time.time() if now is None else now
    p = Plan()
    grace = cfg.lease.grace_s

    if kind == "task":
        # `state.tasks(phase)` walks DESCENDANTS, so sub-tasks nested below a task are
        # candidates too. Filtering on direct parentage excluded them entirely: a task
        # split into two left both halves unreachable, and the queue looked empty while
        # holding the only work there was.
        candidates = state.tasks(phase)
    else:
        candidates = [
            i
            for i in state.items.values()
            if i.kind == kind and not i.removed and (not phase or phase in (i.parent, i.id))
        ]
    p.cycles = find_cycles({i.id: i for i in state.items.values() if not i.removed})
    in_cycle = {n for c in p.cycles for n in c}
    live = state.active_leases(now, grace)

    bugs = bug_items(state, cfg) if cfg.schedule.bugs_first else set()
    for it in sorted(candidates, key=lambda x: (x.id not in bugs, x.priority, x.id)):
        if it.state in (DONE, ABANDONED):
            continue
        if it.state == REVIEW:
            p.review.append(it)
            continue
        if it.state == RUNNING and it.id in live:
            # Running and leased. Always reported as running; never offered as ready,
            # INCLUDING to the agent that holds it -- offering it would put the same
            # item in two lists, and a caller iterating `ready` would start it twice.
            p.running.append(it)
            continue
        blocker = item_blocker(
            state,
            cfg,
            it,
            live,
            agent=agent,
            now=now,
            in_cycle=in_cycle,
            cycles=p.cycles,
        )
        if blocker is not None:
            p.blocked.append(blocker)
        else:
            note = interrupted(state, it, live)
            if note:
                p.interrupted.append(note)
            p.ready.append(it)

    # TWO caps, because they are two different statements and `min()` of them was one
    # number pretending to be one statement:
    #
    #   schedule.max_parallel_tasks — how many items may be IN FLIGHT at once. A
    #     `--no-worktree` lease (a review, a research task) is in flight, so it counts.
    #   worktree.max_parallel       — how many worktrees may EXIST at once. That is a
    #     claim about this machine's disk and CPU, and a lease that never made a tree
    #     consumes neither, so it does not count against it.
    #
    # Under the old `min()` a queue allowed four in flight silently became one on a
    # machine allowed one worktree, whatever the leases were actually doing.
    #
    # The tree cap is applied PER ITEM: an item tagged `no-worktree` (a review, a research
    # task) declares it takes no tree, so a full tree cap does not withhold it and `claim`
    # makes it none (B52). An untagged item is assumed to take a tree.
    #
    # Counted across the WHOLE queue, not the slice this call asked about: `p.running`
    # holds only candidates from `phase`, so with `--phase` the cap was applied against
    # a count of ~0 and two agents each asking about their own phase were both told to
    # go ahead.
    live_items = [i for i in live if i in state.items and not state.items[i].removed]
    in_flight = len(live_items)
    with_trees = len([i for i in live_items if live[i].worktree])
    # The limit in force: the caller's Decision (adaptive or fixed, `services/flowstate`),
    # else `max_parallel_tasks` as it always was. A shrink never touches a running lease:
    # in flight above the limit simply leaves no slot. Floor 1.
    limit = max(1, parallel.limit) if parallel is not None else cfg.schedule.max_parallel_tasks
    paused = parallel is not None and parallel.admit_paused
    flight_slots = 0 if paused else max(0, limit - in_flight)
    if cfg.worktree.enabled:
        # 0 (the default) FOLLOWS the schedule limit; it is not unlimited (B-af-config)
        tree_cap = cfg.worktree.max_parallel or limit
        tree_slots = max(0, tree_cap - with_trees)
    else:
        tree_slots = flight_slots  # no trees are made, so no tree cap applies
    slots = min(flight_slots, tree_slots)
    if slots == flight_slots:
        cap = _cap_words(cfg, parallel, limit)
        live_note = f"{in_flight} in flight across the queue"
        p.cap_note = f"the parallelism cap ({cap})"
        reached = f"parallelism cap reached ({cap})"
    else:
        cap = f"worktree.max_parallel={cfg.worktree.max_parallel}"
        live_note = f"{with_trees} worktrees live across the queue"
        p.cap_note = f"the worktree cap ({cap})"
        reached = f"worktree cap reached ({cap})"
    _cut_ready(state, cfg, p, slots, flight_slots, tree_slots, live_items, live_note, reached, hold)
    if parallel is not None:
        p.parallel_line = parallel_line(cfg, parallel, limit, in_flight, len(p.ready), p.capped)
    # Asked of `unpickable`, the one rule `doctor` reports by; offered, never acted on.
    p.finished = [
        u.item
        for u in unpickable(state, cfg)
        if u.kind == "finished_phase"
        and (not phase or u.item == phase or any(a.id == phase for a in state.ancestors(u.item)))
    ]
    return p


def _words(signal: str) -> str:
    """A controller's ``limited_by`` as people read it: ``load_per_core`` -> "load per
    core"; the controller's own phrases ("ceiling", "independent work") pass through."""
    return signal.replace("_", " ")


def _cap_words(cfg: Config, parallel: FC.Decision | None, limit: int) -> str:
    """What the parallelism cap is, for `cap_note` and "cap reached". Without a Decision,
    or in fixed mode, exactly the words it always had (`_blocking_leases` keys on "cap
    reached"); in auto, the adaptive limit and what limits it."""
    if parallel is None or parallel.mode == "fixed":
        return f"schedule.max_parallel_tasks={cfg.schedule.max_parallel_tasks}"
    if parallel.admit_paused:
        return f"auto: admission paused -- {parallel.reason}"
    return f"auto: limit {limit}, limited by {_words(parallel.limited_by)}"


def parallel_line(
    cfg: Config,
    parallel: FC.Decision,
    limit: int,
    in_flight: int,
    offered: int,
    capped: list[str],
) -> str:
    """The one line `status` and `brief` print: "parallel: 4 (fixed)", or "parallel: 6
    (auto: ceiling 8; limited by <what>)". When nothing is held by the cap -- the offer
    already holds every ready item that can run beside what is in flight -- what limits
    the work is the work itself: "limited by independent work"."""
    if parallel.mode == "fixed":
        return f"parallel: {limit} (fixed)"
    from .flowparams import params

    ceiling = params(cfg).bounds()[2]
    by = parallel.limited_by
    if parallel.admit_paused:
        by = f"{by}: admission paused"
    elif not capped and in_flight + offered <= limit:
        by = FC.INDEPENDENT
    return f"parallel: {limit} (auto: ceiling {ceiling}; limited by {_words(by)})"


def critical_path(state: State, phase: str = "") -> list[str]:
    """Longest dependency chain among not-done items — the true wall-clock floor.

    Reporting it is what stops an operator adding a fifth parallel agent to a phase
    whose runtime is set by a four-deep chain: total time equals the longest path, not
    the sum of the work.
    """

    def before(it: Item) -> list[str]:
        """What must finish before ``it`` can: its dependencies, inherited ones too, and
        -- an umbrella, task or phase, closing only when what is under it does -- its
        children (B79c2f6e17a: without them a split task's chain, or the chain inside a
        phase another depends on, read one step short per level)."""
        deps = [d for _owner, d in inherited_deps(state, it)]
        return deps + [c.id for c in state.children(it.id) if not c.removed]

    live = {i.id: i for i in state.items.values() if not i.removed}
    if phase:
        # The phase, EVERY item below it -- a chain of sub-tasks nested under a task is
        # the phase's work too (B13ed484062) -- and everything those wait on, wherever
        # it lives: a phase that needs another cannot start before that one's chain is
        # done, so that chain is part of this phase's floor.
        inside, todo = set(), [phase]
        while todo:
            n = todo.pop()
            if n in inside or n not in live:
                continue
            inside.add(n)
            todo += before(live[n])
        live = {k: v for k, v in live.items() if k in inside}
    items = live

    # On a cyclic graph the memo is unsound: a result computed under one `seen` set is
    # keyed on the node alone, so a truncated sub-path can be cached and returned where
    # it is wrong. Cycles are reachable via `cycle_policy = "warn"`, so refuse rather
    # than return a confidently wrong number.
    if find_cycles(items, edges=before):
        return []
    memo: dict[str, list[str]] = {}

    def longest(n: str, seen: frozenset[str]) -> list[str]:
        if n in seen:
            return []
        if n in memo:
            return memo[n]
        it = items.get(n)
        best: list[str] = []
        if it:
            # INHERITED dependencies, like readiness uses. The path used to walk
            # `it.needs` alone, so a phase-level dependency did not lengthen the
            # reported floor at all — and the number exists precisely to stop someone
            # adding a fifth agent to a phase whose runtime is set by a chain.
            for dep in before(it):
                if dep in items and items[dep].state != DONE:
                    cand = longest(dep, seen | {n})
                    if len(cand) > len(best):
                        best = cand
        memo[n] = [*best, n]
        return memo[n]

    def trimmed(chain: list[str]) -> list[str]:
        """A phase at the END only closes the chain it holds -- no work of its own, and
        nothing after it waits on it here. Inside a chain it stays: it is the boundary a
        phase dependency waits on. Trimmed BEFORE the chains are compared, or a chain
        long only by its trailing phases beat a longer chain of real work."""
        while chain and items[chain[-1]].kind != "task":
            chain = chain[:-1]
        return chain

    chains = [trimmed(longest(i, frozenset())) for i, it in items.items() if it.state != DONE]
    return max(chains, key=len) if chains else []
