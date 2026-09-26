"""Claim, work, finish: the coordination path.

The densest policy in the package, and until now all of it lived in `surfaces/cli.py`,
reachable only through `main(argv)`. Four rules here exist because each was violated once
and cost something:

* **A claim that is refused releases its lease.** `L.acquire` runs before the
  worktree-conflict check, so returning early left the item leased by an agent that had
  just been told it could not have it — the refusal CREATED the stuck claim `recover`
  exists to clean up, and the caller had no way to know.
* **Adopt before creating.** An agent whose harness already isolated it (Claude Code and
  Cursor both do) was sent to a second tree on a second branch, stranding the uncommitted
  work in the first and giving one item two branches. ddflow does not need to have MADE
  the tree; it needs to know which tree the item is being worked in.
* **Never remove an ADOPTED tree on merge.** ddflow did not create it, the agent's harness
  did, and it may still be working in it. Deleting it takes uncommitted work with it.
* **`merge` goes through the existence check like every other mutating command.** `.get()`
  finds a REMOVED item, because removal is a flag on an item that still folds — so merge
  once landed the branch of work the operator had explicitly dropped, and reported
  success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.model import ABANDONED, DONE
from ..core.plain import plain
from ..infra import worktree as W
from ..services import leases as L
from ._base import _load


def _require(st, item: str, kind: str):
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it


#: What `next` offers when nobody says otherwise. TASKS, because a phase is an umbrella
#: and "work on P1" is not an instruction anyone can act on.
#:
#: Defaulted HERE as well as in argparse, and that duplication is the point: this function
#: was first written with `kind=""`, which `plan()` matches against no item at all, so
#: `ddflow_next` returned an empty queue on every call. The CLI kept working because
#: argparse supplied "task" and the MCP path no longer went through argparse. A default
#: that lives only in the parser is a default the typed layer silently drops.
DEFAULT_NEXT_KIND = "task"

#: `brief` scans for recoverable work unless told not to. On by default because the one
#: moment an agent most needs to know a previous agent crashed mid-task is the moment it
#: is about to start work.
DEFAULT_CHECK_RECOVERY = True


def next_(
    repo: Path, *, kind: str = DEFAULT_NEXT_KIND, phase: str = "", agent: str = ""
) -> O.Outcome:
    """Offer the next actionable item(s). Exit 2 when nothing is actionable."""
    from ..core.schedule import critical_path, plan

    log, cfg, st = _load(repo, agent)
    p = plan(st, cfg, kind=kind, phase=phase, agent=cfg.agent.id or log.agent_id)
    data: dict[str, Any] = {
        "ready": [plain(i) for i in p.ready],
        "blocked": [plain(b) for b in p.blocked],
        "running": [i.id for i in p.running],
        "cycles": p.cycles,
        "interrupted": p.interrupted,
        "critical_path": critical_path(st, phase),
        "_render": {"plan": p},
    }
    if p.ready:
        return O.ok("next", **data)
    return O.nothing("next", f"Nothing actionable ({p.summary()}).", **data)


def _worktree_held_by(st, stored: str, me: str) -> str:
    """Another OPEN item bound to this same worktree, or "".

    Two items sharing one tree cannot be merged or recovered separately: `merge` would
    take one item's branch for the other's work, and `recover` could not say whose
    uncommitted changes it had found. Closed items are ignored — reusing the tree of
    finished work is exactly what an agent should be able to do.
    """
    for item in st.items.values():
        if item.id == me or item.removed or item.state in (DONE, ABANDONED):
            continue
        # `item.worktree`, not `item.lease.worktree`: the fold copies the lease's path
        # onto the item and KEEPS it after the lease is released, which is the point -- a
        # released item whose tree still holds its work is exactly the case that must not
        # be silently co-opted.
        if (item.worktree or "") == stored:
            return item.id
    return ""


def claim(
    repo: Path,
    item: str,
    *,
    globs: str = "",
    note: str = "",
    force: bool = False,
    no_worktree: bool = False,
    called_from: Path | None = None,
    agent: str = "",
) -> O.Outcome:
    """Acquire a lease and (optionally) bind a worktree. Exit 3 if refused.

    Refuses an item that is already looping when `[loops].on_detect = "block"`. That
    refusal is the only thing that actually stops an agent spinning: a warning in a report
    is read by a human later, while a refused claim is read by the agent now.
    """
    from ..config import csv_list
    from ..core import progress as PR
    from ..core.model import fold

    log, cfg, _ = _load(repo, agent)
    events = log.read_all()
    st = fold(events, strict=False)
    looping = [f for f in PR.detect(events, st, cfg) if f.item == item and f.severity == "block"]
    if looping and not force:
        return O.refused(
            "item.claimed",
            f"refusing to claim {item}: it is already looping.\n"
            + "\n".join(f"  {f.render()}" for f in looping)
            + "\n\nRe-claiming it would continue the loop. Change the task, abandon it, "
            "or --force if you have fixed the underlying cause.",
            id=item,
            looping=[f.__dict__ for f in looping],
        )
    want = csv_list(globs) or None
    try:
        lz = L.acquire(log, cfg, item, globs=want, note=note, force=force)
    except L.LeaseError as exc:
        reason = str(exc)
        if exc.alternatives:
            reason += "\n\nYou could take instead: " + ", ".join(exc.alternatives)
        return O.refused("item.claimed", reason, id=item, alternatives=list(exc.alternatives or []))

    wt = None
    if cfg.worktree.enabled and not no_worktree:
        adopted = W.current(called_from or repo) if cfg.worktree.adopt_existing else None
        if adopted is not None:
            stored = W.store_path(repo, adopted.path)
            held = _worktree_held_by(st, stored, item)
            if held:
                # RELEASE before refusing -- see the module docstring.
                L.release(log, item, note="claim refused: worktree conflict")
                return O.refused(
                    "item.claimed",
                    f"this worktree is already bound to {held}, which is still open. "
                    f"Two items sharing one tree cannot be merged or recovered "
                    f"separately. Finish {held}, work somewhere else, or "
                    f"`--no-worktree` to claim without binding a tree.",
                    id=item,
                    conflicts_with=held,
                )
            wt = W.Worktree(
                item=item, path=adopted.path, branch=adopted.branch, base="", created=False
            )
            log.append("worktree.adopted", item, {"path": stored, "branch": wt.branch, "base": ""})
            L.acquire(log, cfg, item, worktree=stored, branch=wt.branch, globs=want, force=True)
        else:
            try:
                wt = W.create(repo, cfg, item)
                stored = W.store_path(repo, wt.path)
                log.append(
                    "worktree.created",
                    item,
                    {"path": stored, "branch": wt.branch, "base": wt.base},
                )
                L.acquire(log, cfg, item, worktree=stored, branch=wt.branch, globs=want, force=True)
            except W.GitError as exc:
                return O.failed(
                    "item.claimed", f"lease held, but worktree creation failed: {exc}", id=item
                )
    log.append("item.started", item, {})
    return O.ok(
        "item.claimed",
        item=item,
        holder=lz.holder,
        worktree=str(wt.path) if wt else "",
        branch=wt.branch if wt else "",
        adopted=bool(wt and not wt.created),
        ttl_s=cfg.lease.ttl_s,
        heartbeat_s=cfg.lease.heartbeat_s,
        base=wt.base if wt else "",
    )


def heartbeat(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    log, _cfg, _st = _load(repo, agent)
    renewed = L.renew(log, item)
    if renewed:
        return O.ok("lease.renewed", id=item, renewed=True)
    return O.nothing("lease.renewed", f"no lease held {item}", id=item, renewed=False)


def release(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    log, _cfg, _st = _load(repo, agent)
    released = L.release(log, item, note=note)
    if released:
        return O.ok("lease.released", id=item, released=True)
    return O.nothing("lease.released", f"no lease on {item}", id=item, released=False)


def complete(
    repo: Path,
    item: str,
    *,
    sha: str = "",
    force: bool = False,
    model: str = "",
    agent: str = "",
) -> O.Outcome:
    """Finish an item, refusing on an incomplete pipeline unless forced.

    The rule-set lives in `services.completion`. This decides only what to DO with the
    verdict, and records the override when one is taken — an unrecorded `--force` is a
    pipeline that was never really enforced.
    """
    from ..services import completion as CM

    log, cfg, st = _load(repo, agent)
    it = _require(st, item, "item.completed")
    if isinstance(it, O.Outcome):
        return it

    v = CM.verdict(st, cfg, item, repo=repo, model=model)
    base: dict[str, Any] = {
        "id": item,
        "sha": sha,
        "independence": v.independence,
        # BOTH surfaces, always. This used to print only in human mode, so an agent over
        # MCP -- which is always JSON -- completed the item and was never told a gate had
        # not run: the one fact most worth surfacing, invisible on precisely the surface
        # that needed it.
        "coverage_gaps": v.coverage_gaps,
        "note": v.coverage_note,
        "warnings": v.warnings,
        "blockers": v.blockers,
    }
    if not v.may_complete and not force:
        return O.refused(
            "item.completed",
            f"cannot complete {item} — {len(v.blockers)} unmet condition(s):\n"
            + "\n".join(f"  - {b}" for b in v.blockers)
            + f"\n\n`ddflow gate status {item}` shows the pipeline. --force overrides, "
            f"and the override is recorded.",
            forced=False,
            **base,
        )
    forced = bool(v.blockers and force)
    log.append(
        "item.completed",
        item,
        {
            "sha": sha,
            "kind": it.kind,
            "forced": forced,
            "overridden": v.blockers if force else [],
        },
    )
    L.release(log, item, note="completed")
    return O.ok("item.completed", forced=forced, **base)


def abandon(
    repo: Path, item: str, *, reason: str = "", force: bool = False, agent: str = ""
) -> O.Outcome:
    """Stop work without completing, with a recorded reason.

    Distinct from `block`: a blocked item is waiting for something and will resume, an
    abandoned one will not. The phase completion check treats only `done` and `abandoned`
    as settled, so an item you decided against stops holding its phase open — which it
    otherwise does forever, since nothing else can ever finish it.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.abandoned")
    if isinstance(it, O.Outcome):
        return it
    if it.state == DONE and not force:
        return O.refused(
            "item.abandoned",
            f"{item} is already done; abandoning it would rewrite finished history. "
            f"--force if you really mean it.",
            id=item,
            reason=reason,
        )
    log.append("item.abandoned", item, {"reason": reason, "kind": it.kind})
    if it.lease:
        L.release(log, item, note=f"abandoned: {reason}")
    return O.Outcome(kind="item.abandoned", data={"id": item, "reason": reason})


def remove(
    repo: Path, item: str, *, reason: str = "", force: bool = False, agent: str = ""
) -> O.Outcome:
    """Take an item out of the queue entirely.

    The event log is append-only, so this RECORDS a removal rather than deleting anything:
    the item and everything that happened to it stay in the history and in `ddflow
    replay`, which is what keeps the record honest about work that was planned and then
    dropped.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.removed")
    if isinstance(it, O.Outcome):
        return it
    # Any item with work beneath it, not just a phase. The `kind == "phase"` guard
    # predates sub-tasks: removing a task umbrella left its children live but unreachable,
    # because `State.tasks(phase)` walks `descendants()` and `children()` skips a removed
    # node -- so the phase view reported "nothing actionable" while two open tasks sat
    # under the hole.
    kids = [t.id for t in st.open_descendants(item)]
    if kids and not force:
        return O.refused(
            "item.removed",
            f"{item} still has {len(kids)} task(s): {', '.join(kids[:8])}.\n"
            f"Remove them first, or --force to orphan them.",
            id=item,
            children=kids,
        )
    dependents = [o.id for o in st.items.values() if not o.removed and item in o.needs]
    if dependents and not force:
        return O.refused(
            "item.removed",
            f"{', '.join(dependents)} depend{'s' if len(dependents) == 1 else ''} on "
            f"{item}. Removing it would leave them blocked on something that no longer "
            f"exists (unknown dependencies are treated as unmet, deliberately).\n"
            f"Update them first, or --force.",
            id=item,
            dependents=dependents,
        )
    if it.lease:
        L.release(log, item, note="removed from the queue")
    log.append("phase.removed" if it.kind == "phase" else "task.removed", item, {"reason": reason})
    return O.ok("item.removed", id=item, children=[], dependents=[])


def block(repo: Path, item: str, *, reason: str = "", agent: str = "") -> O.Outcome:
    """Park an item on something outside the queue — a vendor, an operator decision.

    The existence check is not ceremony. `fold`'s `_h_state` reaches items through
    `_item()`, which CREATES one when the id is unknown, so this was the only mutating
    command where a typo'd id materialised a titleless phantom task — which the scheduler
    then offered to an agent as the next thing to do.
    """
    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.blocked")
    if isinstance(it, O.Outcome):
        return it
    log.append("item.blocked", item, {"reason": reason})
    return O.Outcome(kind="item.blocked", data={"id": item, "reason": reason})


def merge(
    repo: Path,
    item: str,
    *,
    message: str = "",
    allow_dirty: bool = False,
    keep: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Land an item's branch. The most consequential action in the package."""
    from ..services import gates as G

    log, cfg, st = _load(repo, agent)
    it = _require(st, item, "worktree.merged")
    if isinstance(it, O.Outcome):
        return it
    if not it.worktree:
        return O.nothing("worktree.merged", f"{item} has no worktree to merge", id=item, dirty=[])

    wt = W.Worktree(
        item=item,
        path=W.load_path(repo, it.worktree),
        branch=it.branch,
        base=cfg.worktree.base_ref or W.default_branch(repo),
    )
    dirty = W.dirty(wt)
    if dirty and not allow_dirty:
        # LISTED, not just counted: half the time these are build artefacts the project
        # forgot to gitignore, and half the time they are a source file the agent never
        # `git add`-ed -- which would be silently dropped from the merge. The caller can
        # only tell which by seeing the names.
        return O.refused(
            "worktree.merged",
            f"{len(dirty)} uncommitted file(s) in {wt.path} would NOT be included in "
            f"the merge.\n\nCommit them, add them to .gitignore if they are build "
            f"output, or pass --allow-dirty to merge without them.",
            id=item,
            dirty=list(dirty),
            path=str(wt.path),
        )
    sha = W.head_sha(wt.path)
    r = W.merge(repo, cfg, wt, message=message or f"merge {item}: {it.title}")
    if not r.ok:
        out = O.Outcome(
            kind="worktree.merged",
            data={"id": item, "sha": "", "dirty": []},
            exit=O.REFUSED if r.code == W.GIT_REFUSED else O.FAIL,
            reason=r.err or r.out,
        )
        return out
    log.append("worktree.merged", item, {"sha": sha, "branch": wt.branch})
    G.record(
        log,
        cfg,
        item,
        "merge",
        "passed",
        evidence={"sha": sha, "branch": wt.branch},
        gates=G.load_gates(repo, cfg),
    )
    # NEVER remove an ADOPTED tree -- see the module docstring.
    kept_reason = ""
    removed_tree = False
    if it.adopted:
        kept_reason = f"worktree {wt.path} kept: adopted, not created by ddflow."
    elif cfg.worktree.remove_on_merge and not keep:
        rr = W.remove(repo, cfg, wt)
        if rr.ok:
            log.append("worktree.removed", item, {"path": str(wt.path)})
            removed_tree = True
        else:
            kept_reason = rr.err
    return O.ok(
        "worktree.merged",
        id=item,
        sha=sha,
        base=wt.base,
        dirty=list(dirty),
        worktree_removed=removed_tree,
        kept_reason=kept_reason,
    )


def brief(
    repo: Path,
    *,
    item: str = "",
    phase: str = "",
    check_recovery: bool = DEFAULT_CHECK_RECOVERY,
    agent: str = "",
) -> O.Outcome:
    """The budgeted reading pack: what to do next, and what governs it.

    Decisions reach the agent by GLOB rather than by search — the whole point is that they
    arrive without its having to suspect they exist.
    """
    from ..core.schedule import conflicts, plan
    from ..infra.store import Store
    from ..views import markdown as render_md

    log, cfg, _ = _load(repo, agent)
    store = Store(repo, cfg)
    st = store.ensure(log)
    p = plan(st, cfg, phase=phase, agent=cfg.agent.id or log.agent_id)
    if not item and p.ready:
        item = p.ready[0].id

    query = ""
    if item and item in st.items:
        target = st.items[item]
        query = f"{target.title} {target.body} {' '.join(target.tags)}"
    lessons = store.search("lessons", query, cfg.session.brief_lesson_count) if query else []

    rules = ""
    for candidate in ("AGENTS.md", "CLAUDE.md", ".ddflow/RULES.md"):
        if (repo / candidate).is_file():
            rules = f"See `{candidate}` (loaded separately by your agent)."
            break

    recovery = L.scan(log, cfg, repo) if check_recovery else []
    decisions = []
    if item and item in st.items:
        target = st.items[item]
        decisions = [
            d
            for d in st.decisions.values()
            if d.live and d.globs and conflicts(target.globs, d.globs)
        ]
        decisions += [d for d in st.decisions.values() if d.live and not d.globs]

    text = render_md.brief(
        st,
        cfg,
        p,
        repo=repo,
        item=item,
        lessons=lessons,
        rules=rules,
        recovery=[r for r in recovery if r.salvageable],
        decisions=decisions,
    )
    return O.ok(
        "brief",
        brief=text,
        text=text,
        item=item,
        ready=[i.id for i in p.ready],
        approx_tokens=len(text) // 4,
    )
