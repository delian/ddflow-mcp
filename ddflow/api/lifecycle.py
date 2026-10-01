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

import time
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.model import ABANDONED, DONE, REVIEW, State
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
    promoted: list[str] = []
    if cfg.flow.auto_promote:
        # Continuous delivery where the operator asked for it: an environment in
        # auto_promote whose upstream moved gets its promotion filed here, and offered
        # below like any other task.
        from ..services import promotions as PM

        promoted = PM.auto(repo, cfg, log, st)
        if promoted:
            log, cfg, st = _load(repo, agent)
    synced: dict[str, Any] = {}
    if (
        cfg.flow.integration == "pr"
        and cfg.flow.sync_on_next
        and any(i.state == REVIEW for i in st.items.values())
    ):
        # Reviewers act between an agent's turns. Asking here is what lets a merged
        # request complete, and a requested change come back as work, without anyone
        # remembering to run `pr sync` -- the loop stays `next`, `claim`, work, `merge`.
        from ..services import flow as FS

        rep = FS.sync(repo, cfg, log)
        synced = {
            "changes": [f"{c.item}: {c.what}" for c in rep.changes],
            "unavailable": rep.unavailable,
        }
        if rep.changes:
            log, cfg, st = _load(repo, agent)
    p = plan(st, cfg, kind=kind, phase=phase, agent=cfg.agent.id or log.agent_id)
    data: dict[str, Any] = {
        "review": [i.id for i in p.review],
        "synced": synced,
        "promoted": promoted,
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
    return O.nothing("next", f"Nothing actionable ({p.summary()}).{_wait_hint(p)}", **data)


def _wait_hint(p) -> str:
    """Point a blocked caller at `wait` -- only when something in flight can clear it."""
    if p.running and any(b.reason in WAITABLE for b in p.blocked):
        return (
            " `ddflow wait` sleeps until one of the blocked items frees, and returns "
            "the moment it does — no need to poll or to ask a person."
        )
    return ""


#: Blocker reasons that clear on their own when another agent finishes. Everything else
#: -- a cycle, a contest, an operator's hold, a release line gone from config, an
#: umbrella -- needs someone to ACT, and waiting on it would idle until the deadline and
#: look like progress while doing so.
WAITABLE = frozenset({"conflict", "deps", "resources"})

#: How long `wait` blocks when the caller does not say. Long enough to outlast most
#: holders' remaining work, short enough that a forgotten wait does not hold a process
#: for an afternoon. A caller that wants longer asks again, which also re-checks that
#: waiting is still the right move.
DEFAULT_WAIT_TIMEOUT_S = 600


def _judge_wait(st, cfg, me: str, item: str, phase: str, kind: str) -> dict[str, Any]:
    """Can the caller start work NOW, must it wait, or would waiting never end?

    Returns {"status": "ready"|"blocked"|"hopeless", "why", "waiting_on", "ready",
    "blocked"}. "hopeless" is the case that matters most: a wait that no other agent's
    progress can end is a stall dressed as patience, so it is refused up front with the
    move that WOULD help.
    """
    now = time.time()
    live = st.active_leases(now, cfg.lease.grace_s)
    others = {i: lz for i, lz in live.items() if lz.holder != me}
    if not item:
        return _judge_any(st, cfg, me, phase, kind, now, live)
    it = st.items.get(item)
    out: dict[str, Any] = {"why": "", "waiting_on": [], "ready": [], "blocked": []}
    if it is None or it.removed:
        return {**out, "status": "hopeless", "why": f"{item} was removed from the queue"}
    if it.state in (DONE, ABANDONED):
        return {**out, "status": "hopeless", "why": f"{item} is already {it.state}"}
    if it.state == REVIEW:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} is in review: its reviewers have it, not an agent. "
            f"`ddflow pr sync` brings it back if they request changes.",
        }
    mine = live.get(item)
    if mine is not None and mine.holder == me:
        return {**out, "status": "ready", "why": f"you already hold {item}", "ready": [item]}
    b = _claim_blocker(st, cfg, it, me, live, now)
    if b is None:
        return {**out, "status": "ready", "why": f"{item} is free to claim", "ready": [item]}
    out["blocked"] = [plain(b)]
    if b.reason == "expired":
        return {**out, "status": "hopeless", "why": b.detail}
    # No "cap reached" blocker arrives here: the parallelism cap trims `plan`'s ready
    # LIST and `claim` does not apply it to a named item, so `_claim_blocker` never
    # returns it (pinned by test_a_cap_blocked_item_wait_agrees_with_claim). Only the
    # any-wait meets the cap, in `_blocking_leases`.
    if b.reason not in WAITABLE:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item}: {b.reason} — {b.detail}. That does not clear when another "
            f"agent finishes; someone has to act on it.",
        }
    if b.reason == "resources":
        theirs = sorted(i for i, lz in others.items() if lz.resources)
        if not theirs:
            held = sorted(i for i, lz in live.items() if lz.holder == me and lz.resources)
            return {
                **out,
                "status": "hopeless",
                "why": f"{item}: {b.detail}, and only "
                + (f"{', '.join(held)}, which YOU hold," if held else "nobody")
                + " holds that resource. Finish or release it first; waiting on "
                "yourself never ends.",
            }
        return {
            **out,
            "status": "blocked",
            "why": f"{item}: {b.reason} — {b.detail}",
            "waiting_on": theirs,
        }
    if b.reason == "conflict":
        return {
            **out,
            "status": "blocked",
            "why": f"{item}: {b.detail}",
            "waiting_on": b.waiting_on,
        }
    held_by_me = [d for d in b.waiting_on if d in live and live[d].holder == me]
    if held_by_me:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} waits on {', '.join(held_by_me)}, which YOU hold. "
            f"Finish that first; waiting on yourself never ends.",
        }
    moving = [d for d in b.waiting_on if _in_motion(st, d, others)]
    if not moving:
        return {
            **out,
            "status": "hopeless",
            "why": f"{item} waits on {', '.join(b.waiting_on) or 'its dependencies'}, "
            f"and nobody is working on them. Take one of them instead of waiting.",
        }
    return {**out, "status": "blocked", "why": f"{item}: deps — {b.detail}", "waiting_on": moving}


def _judge_any(st, cfg, me: str, phase: str, kind: str, now: float, live) -> dict[str, Any]:
    """`_judge_wait` for "anything": the same ready set `next` offers."""
    from ..core.schedule import plan

    others = {i: lz for i, lz in live.items() if lz.holder != me}
    p = plan(st, cfg, kind=kind, phase=phase, now=now, agent=me)
    out: dict[str, Any] = {
        "why": "",
        "waiting_on": [],
        "ready": [],
        "blocked": [plain(b) for b in p.blocked],
    }
    # Only what `claim` would grant: under `ready_policy = deps_only` the plan offers
    # items another agent's lease or globs still cover.
    refused = [(i, _claim_blocker(st, cfg, i, me, live, now)) for i in p.ready]
    ready = [i.id for i, b in refused if b is None]
    if ready:
        return {**out, "status": "ready", "ready": ready}
    p.blocked.extend(b for _i, b in refused)
    out["blocked"] = [plain(b) for b in p.blocked]
    if not others:
        return {
            **out,
            "status": "hopeless",
            "why": f"nothing is ready and no other agent holds anything ({p.summary()}), "
            "so no release is coming to wake you. `ddflow next` says what blocks the "
            "queue — it needs someone to act, not to wait.",
        }
    blocking = _blocking_leases(st, p.blocked, others)
    return {
        **out,
        "status": "blocked",
        "why": f"nothing is ready ({p.summary()}); waiting on "
        + ", ".join(f"{i} ({others[i].holder})" for i in blocking),
        "waiting_on": blocking,
    }


def _blocking_leases(st, blocked, others) -> list[str]:
    """The held items whose release could free something in ``blocked``.

    What the waiter registers against, and so which holders are told someone waits on
    them. Every lease in flight was the first answer, and it told an unrelated holder at
    each heartbeat that it was holding someone up. A full cap or a resource shortfall is
    freed by ANY release, and when nothing names a holder the answer stays "all of them":
    over-reporting a waiter is harmless, under-reporting hides one.
    """
    named: set[str] = set()
    for b in blocked:
        if b.reason == "resources" or (b.reason == "state" and "cap reached" in b.detail):
            return sorted(others)
        for w in b.waiting_on:
            if w in others:
                named.add(w)
            elif w in st.items:
                named.update(k.id for k in st.open_descendants(w) if k.id in others)
    return sorted(named) or sorted(others)


def _claim_blocker(st, cfg, it, me: str, live, now: float):
    """Why `claim` would refuse ``it`` for ``me`` right now, as a `Blocked`; None if not.

    The scheduler's predicate plus the checks `claim` applies unconditionally -- an
    expired lease under `reclaim_policy = "report"`, another holder's live lease on the
    item, overlapping globs, resource capacity.
    `item_blocker` skips these under `ready_policy = deps_only`, and waking on a
    verdict `claim` then refuses would spin (roborev: it did, for a held item).
    """
    from ..core.schedule import (
        Blocked,
        capacities,
        find_cycles,
        item_blocker,
        resource_shortfall,
    )

    cycles = find_cycles({i.id: i for i in st.items.values() if not i.removed})
    in_cycle = {n for c in cycles for n in c}
    b = item_blocker(st, cfg, it, live, agent=me, now=now, in_cycle=in_cycle, cycles=cycles)
    if b is not None:
        return b
    lz = it.lease
    if (
        lz is not None
        and it.id not in live
        and lz.holder != me
        and cfg.lease.reclaim_policy == "report"
    ):
        # Expiry frees the holder's GLOBS for everyone else, but not its item: `claim`
        # refuses an expired lease, because a crashed agent's tree often holds finished
        # work. Calling it claimable made wait -> refused -> wait a spin -- for the item
        # path, and (critic) for an any-wait under deps_only, whose ready set is
        # filtered through here.
        return Blocked(
            it.id,
            "expired",
            f"{it.id}'s lease from {lz.holder} has expired and is not handed on "
            f"automatically. `ddflow recover --item {it.id}` says what its tree holds; "
            f"salvage, release it, then claim.",
            [],
        )
    held = live.get(it.id)
    if held is not None and held.holder != me:
        return Blocked(it.id, "conflict", f"leased by {held.holder}", [it.id])
    clash = L.glob_clash(st, cfg, it, me, list(it.globs), now)
    if clash is not None:
        other_id, lz, pair = clash
        return Blocked(
            it.id,
            "conflict",
            f"globs overlap {other_id} held by {lz.holder} ({pair[0]} vs {pair[1]})",
            [other_id],
        )
    if it.resources and it.id not in live:
        try:
            short = resource_shortfall(it.resources, live, capacities(cfg), exclude=it.id)
        except ValueError:
            short = ""  # a declaration error: claim reports it, and waiting cannot fix it
        if short:
            return Blocked(it.id, "resources", short, [])
    return None


def _in_motion(st, dep: str, others: dict) -> bool:
    """Will `dep` finish without the caller? Held by another agent, under review, in
    another repository, or with a sub-task someone holds."""
    from ..core.schedule import is_external

    if is_external(dep) or dep in others:
        return True
    d = st.items.get(dep)
    if d is None:
        return False
    if d.state == REVIEW:
        return True
    return any(k.id in others for k in st.open_descendants(dep))


def _freed(st, cfg, was: list[str], me: str) -> list[str]:
    """What happened to each item the caller was waiting on -- the answer to "why did
    I wake?", in the words an agent can relay."""
    now = time.time()
    live = st.active_leases(now, cfg.lease.grace_s)
    out: list[str] = []
    for i in was:
        it = st.items.get(i)
        if it is None:
            out.append(f"{i}: gone")
        elif it.state in (DONE, ABANDONED):
            out.append(f"{i}: {it.state}")
        elif i not in live:
            lz = it.lease
            out.append(f"{i}: lease {'expired' if lz else 'released'}")
        elif live[i].holder == me:
            out.append(f"{i}: now yours")
        else:
            out.append(f"{i}: still held by {live[i].holder}")
    return out


def wait(
    repo: Path,
    *,
    item: str = "",
    phase: str = "",
    kind: str = DEFAULT_NEXT_KIND,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    agent: str = "",
    on_progress=None,
) -> O.Outcome:
    """Block until ``item`` (or, without one, anything) can be started. Exit 0 on wake.

    Exit 2 when the deadline passes with it still blocked, and at once -- without
    sleeping -- when waiting cannot help: the item is done, in review, in a cycle, held
    by an operator, or waits on work nobody is doing. ``timeout_s=0`` asks the question
    without waiting at all.

    Waking is a hint, not a reservation: two agents waiting on one release both wake,
    and one of them loses the `claim`. The loser is refused with alternatives and can
    wait again. A queue that reserved on wake would need the waiter to be alive to use
    the reservation, which is the crash-recovery problem leases already solve.
    """
    from ..services import waits as WT

    timeout = DEFAULT_WAIT_TIMEOUT_S if timeout_s is None else float(timeout_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    empty: dict[str, Any] = {
        "item": item,
        "phase": phase,
        "woke": False,
        "waitable": False,
        "ready": [],
        "waiting_on": [],
        "freed_by": [],
        "blocked": [],
        "waited_s": 0,
    }
    if timeout < 0 or poll <= 0:
        return O.failed("wait", "timeout must be >= 0 and poll > 0 seconds", **empty)
    log, cfg, st = _load(repo, agent)
    if item:
        found = _require(st, item, "wait")
        if isinstance(found, O.Outcome):
            return O.Outcome("wait", {**empty, **found.data}, found.exit, found.reason)
    me = cfg.agent.id or log.agent_id
    say = on_progress or (lambda _msg: None)

    def result(v: dict[str, Any], waited: float, freed: list[str]) -> O.Outcome:
        data = {
            **empty,
            "woke": v["status"] == "ready",
            "waitable": v["status"] != "hopeless",
            "ready": v["ready"],
            "waiting_on": v["waiting_on"],
            "freed_by": freed,
            "blocked": v["blocked"],
            "waited_s": round(waited),
        }
        if v["status"] == "ready":
            data["advice"] = (
                f"`ddflow claim {v['ready'][0]}` now: anyone else waiting on the same "
                f"release woke too."
            )
            return O.ok("wait", **data)
        if v["status"] == "hopeless":
            return O.nothing("wait", f"Not waiting: {v['why']}", **data)
        return O.nothing(
            "wait",
            f"Still blocked after {round(waited)}s: {v['why']}. `ddflow wait` again to "
            f"keep waiting.",
            **data,
        )

    v = _judge_wait(st, cfg, me, item, phase, kind)
    if v["status"] != "blocked" or timeout == 0:
        return result(v, 0.0, [])

    started = time.monotonic()
    deadline = started + timeout
    w = WT.register(
        repo,
        WT.Waiter(
            agent=me,
            item=item,
            phase=phase,
            waiting_on=v["waiting_on"],
            reason=v["why"],
            until=time.time() + timeout,
        ),
    )
    say(f"waiting (up to {round(timeout)}s): {v['why']}")
    # None, so the first pass re-judges whatever landed between the load above and
    # the registration -- a release in that gap must not cost a whole RECHECK_S.
    seen: dict[str, int] | None = None
    checked = started
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return result(v, time.monotonic() - started, [])
            time.sleep(min(poll, left))
            # Fingerprint BEFORE the read, never after: see `EventLog.extent`.
            ext = log.extent()
            if ext == seen and time.monotonic() - checked < WT.RECHECK_S:
                continue
            seen, checked = ext, time.monotonic()
            log, cfg, st = _load(repo, agent)
            was = v
            v = _judge_wait(st, cfg, me, item, phase, kind)
            if v["status"] != "blocked":
                freed = _freed(st, cfg, was["waiting_on"], me)
                if freed:
                    say("woke: " + "; ".join(freed))
                return result(v, time.monotonic() - started, freed)
            if v["why"] != was["why"]:
                say(f"still waiting: {v['why']}")
                WT.update(w, waiting_on=v["waiting_on"], reason=v["why"])
    finally:
        WT.unregister(w)


def _worktree_held_by(st, stored: str, me: str, repo: Path | None = None, cfg=None) -> str:
    """Another OPEN item bound to this same worktree, or "".

    Two items sharing one tree cannot be merged or recovered separately: `merge` would
    take one item's branch for the other's work, and `recover` could not say whose
    uncommitted changes it had found. Closed items are ignored — reusing the tree of
    finished work is exactly what an agent should be able to do. So is an open item
    that has let go of the tree and left nothing in it (`_tree_let_go`), given ``repo``.
    """
    for item in st.items.values():
        if item.id == me or item.removed or item.state in (DONE, ABANDONED):
            continue
        # `item.worktree`, not `item.lease.worktree`: the fold copies the lease's path
        # onto the item and KEEPS it after the lease is released, which is the point -- a
        # released item whose tree still holds its work is exactly the case that must not
        # be silently co-opted.
        if (item.worktree or "") == stored:
            if repo is not None and _tree_let_go(repo, cfg, st, item):
                continue
            return item.id
    return ""


def callers_tree(repo: Path, cfg, st, it, called_from: Path | None) -> tuple[Any, str]:
    """(the linked worktree the caller stands in, or None; the OTHER item working it, or "").

    For an item claimed without a worktree, where the caller stands is the best evidence
    of where its work is -- unless another item is being worked there. Run from item B's
    tree, item A's gate ran B's suite and recorded it as A's pass (found by the critic
    review of B8be9373cf5's fix). Shared by `gate run`/`record`, `review` and `merge`, so
    they cannot disagree about whose tree it is.

    "Being worked" is a LIVE claim: another open item bound to the tree with its lease
    held. Not `claim`'s stricter held-until-let-go rule, which protects a released item's
    leftover work from being co-opted: a tree bound to an item that was merged and let go
    of its lease, carrying newer commits, is exactly where a `--no-worktree` item is
    worked -- the case this feature exists for -- and treating it as foreign made that
    item's own gates unavailable.
    """
    here = W.current(called_from or repo)
    if here is None:
        return None, ""
    stored = W.store_path(repo, here.path)
    for other in st.items.values():
        if other.id == it.id or other.removed or other.state in (DONE, ABANDONED):
            continue
        if (other.worktree or "") == stored and other.lease is not None:
            return None, other.id
    return here, ""


def _tree_let_go(repo: Path, cfg, st, item) -> bool:
    """True when an OPEN item's tree holds none of its work: lease released, merged, and
    nothing in the tree that its merge target lacks -- no uncommitted change, and a HEAD
    the target already contains.

    Open is not the same as occupying. B-release-every-push was merged and released, open
    only for a review the operator deferred, and still refused the next claim in its tree
    (B0ff09a29a5) -- whose way out, `--no-worktree`, then broke `merge` and `review`. An
    expired but unreleased lease still holds: that is `recover`'s, not a claim's.
    """
    from ..services import flow as FS

    if item.lease is not None or not item.merged_sha:
        return False
    tree = W.load_path(repo, item.worktree)
    if not tree.is_dir():
        return False
    if W.dirty(W.Worktree(item=item.id, path=tree, branch=item.branch, base="")):
        return False
    target = FS.target(repo, cfg, item, st)
    return W.git(tree, "merge-base", "--is-ancestor", "HEAD", target).ok


def _tree_of(start: Path) -> Path | None:
    """The git working tree `start` lies in (primary or linked), resolved; None if none.

    The same question `services.enforce._this_worktree` asks of the hook's cwd, asked of
    an explicit start instead: `--repo` is resolved to the primary, so the cwd of this
    process is not where the caller is standing.
    """
    top = W.git(start, "rev-parse", "--show-toplevel", timeout=30)
    return Path(top.out).resolve() if top.ok and top.out else None


def _in_leased_tree(repo: Path, stored: str, here: Path | None) -> bool:
    """Is `here` the tree a lease records? The same test `check_commit` applies."""
    return bool(here and stored and W.load_path(repo, stored).resolve() == here)


def _recorded_tree(repo: Path, it) -> Path | None:
    """The item's own tree from an earlier claim, if it is still on disk; else None.

    Such a tree may hold commits nothing has merged yet. A re-claim must bind to it,
    not to wherever the caller happens to stand, or `merge` merges an empty branch and
    the salvage is orphaned.
    """
    if it is None or not it.worktree or not it.branch:
        return None
    path = W.load_path(repo, it.worktree).resolve()
    return path if _is_items_tree(repo, path, it.branch) else None


def _is_items_tree(repo: Path, path: Path, branch: str) -> bool:
    """Is the tree at `path` still the one that carries `branch`'s work?

    ONE rule for every place a claim would bind an existing tree -- the item's recorded
    tree and the one `W.create` finds at the default path. A directory there is not
    proof: it can have been removed and re-added on something unrelated. Git must list
    it as a worktree of this repository, with `branch` checked out -- or detached (a
    rebase in progress, a checkout at a commit) with `branch` an ancestor of HEAD. Any
    other branch, or a detached HEAD holding unrelated work, is someone else's tree.
    """
    path = Path(path).resolve()
    if not branch or not (path.is_dir() and (path / ".git").exists()):
        return False
    entry = next(
        (e for e in W.list_worktrees(repo) if Path(e.get("worktree", "")).resolve() == path),
        None,
    )
    if entry is None:
        return False
    if entry.get("branch") == f"refs/heads/{branch}":
        return True
    return bool(
        entry.get("detached")
        and W.git(path, "merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD").ok
    )


def _undo_claim(log, item: str, held_before: bool, note: str) -> None:
    """Take back what a refused claim acquired -- and ONLY that.

    A fresh claim that is refused releases its lease, or the refusal itself creates the
    stuck claim `recover` exists to clean up. But a caller that already held a live lease
    on the item got a renewal, not a new lease: releasing it would hand an item whose
    tree holds that caller's work to anyone who asks.
    """
    if not held_before:
        L.release(log, item, note=note)


def claim(
    repo: Path,
    item: str,
    *,
    globs: str = "",
    note: str = "",
    force: bool = False,
    no_worktree: bool = False,
    called_from: Path | None = None,
    resources: str = "",
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
    parked = st.items.get(item)
    if parked is not None and parked.state == REVIEW and not force:
        # Claiming it would let a push ride into the request its reviewers already
        # judged -- and while it ran, `pr sync` would stop watching the request at all.
        return O.refused(
            "item.claimed",
            f"{item} is in review ({parked.pr.url if parked.pr else 'its request'}); the "
            f"reviewers have it. `ddflow pr sync` brings it back if they request changes. "
            f"--force to take it back anyway.",
            id=item,
            alternatives=[],
        )
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
    # A holder re-claiming its own LIVE lease only renews it; a refusal below must not
    # then take away a lease the caller already had, with its tree full of work.
    prior = st.items[item].lease if item in st.items else None
    held_before = bool(
        prior
        and prior.holder == log.agent_id
        and not prior.expired_at
        and not prior.expired(time.time(), cfg.lease.grace_s)
    )
    try:
        lz = L.acquire(
            log,
            cfg,
            item,
            globs=want,
            note=note,
            force=force,
            resources=csv_list(resources) or None,
        )
    except L.LeaseError as exc:
        reason = str(exc)
        if exc.alternatives:
            reason += "\n\nYou could take instead: " + ", ".join(exc.alternatives)
        own = prior if prior is not None and prior.holder == exc.holder else None
        lapsed = own is not None and bool(
            own.expired_at or own.expired(time.time(), cfg.lease.grace_s)
        )
        if exc.holder and exc.holder != log.agent_id and not lapsed:
            # The refusal is the moment an agent decides between idling, polling and
            # asking a person. None of the three is needed when the block is a live
            # holder: `wait` wakes it when that holder lets go. Not for an EXPIRED lease,
            # which `wait` refuses at once -- that one is `recover`'s.
            reason += (
                f"\n\nOr `ddflow wait --item {item}`: it sleeps until {exc.holder} lets "
                f"go and returns the moment the item can be claimed."
            )
        return O.refused("item.claimed", reason, id=item, alternatives=list(exc.alternatives or []))

    wt = None
    ours = False  # a tree ddflow made (now or on an earlier claim), not one it adopted
    rebound = False  # bound to the item's OWN recorded tree from an earlier claim
    was_adopted = False
    recorded = _recorded_tree(repo, st.items.get(item))
    if cfg.worktree.enabled and not no_worktree and recorded is not None:
        # The item already has a tree, and it still exists: that tree -- and its branch,
        # with whatever unmerged work is on it -- is the item's, wherever the caller is
        # standing. Adopting the caller's tree instead made `merge` merge nothing.
        it = st.items[item]
        branch = it.branch
        stored = W.store_path(repo, recorded)
        wt = W.Worktree(item=item, path=recorded, branch=branch, base=it.base, created=False)
        L.acquire(log, cfg, item, worktree=stored, branch=branch, globs=want, force=True)
        rebound = True
        was_adopted = bool(it.adopted)
        ours = not it.adopted
    elif cfg.worktree.enabled and not no_worktree:
        adopted = W.current(called_from or repo) if cfg.worktree.adopt_existing else None
        if adopted is not None:
            stored = W.store_path(repo, adopted.path)
            held = _worktree_held_by(st, stored, item, repo, cfg)
            if held:
                # RELEASE before refusing -- see the module docstring.
                _undo_claim(log, item, held_before, "claim refused: worktree conflict")
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
                from ..services import flow as FS

                base, branch = ("", "")
                if item in st.items:
                    # The branching model decides the fork point: gitflow's develop or
                    # production, or a dependency's unmerged branch when stacking.
                    base, branch = FS.fork_point(repo, cfg, st, st.items[item])
                wt = W.create(repo, cfg, item, base=base, branch=branch)
                if not wt.created:
                    # `W.create` reuses whatever tree sits at the default path. One on
                    # ANOTHER branch is not this item's: binding it recorded a branch
                    # that is not checked out there, and `merge` merged the wrong work.
                    if not _is_items_tree(repo, wt.path, wt.branch):
                        head = W.git(wt.path, "rev-parse", "--abbrev-ref", "HEAD")
                        there = head.out if head.ok else "something else"
                        there = "a detached HEAD" if there == "HEAD" else there
                        _undo_claim(log, item, held_before, "claim refused: worktree path occupied")
                        return O.refused(
                            "item.claimed",
                            f"{wt.path} already exists with {there} checked out, which "
                            f"does not carry {wt.branch}. It is not {item}'s tree; move or "
                            f"remove it, or claim from a tree of your own.",
                            id=item,
                            path=str(wt.path),
                        )
                ours = True
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
    # The first branch made is where the branching model starts to matter. An unmade
    # choice is defaulted here, on the record, and followed from now on.
    from ..services import choices as CH

    CH.adopt_defaults(log, cfg, ["model", "integration"])
    port: dict[str, Any] = {}
    target_item = st.items.get(item)
    if (
        target_item is not None
        and (target_item.port_from or target_item.promote_to)
        and not target_item.port
    ):
        from ..services import ports as PT

        # Applied in a tree ddflow made -- also on a RE-claim, which is how a port claimed
        # with --force before its source landed gets applied once it has. In an adopted
        # tree, or with no tree, ddflow does not rewrite the agent's files: it says what
        # to do instead of staying silent about it being a port at all.
        port = (
            PT.apply(repo, cfg, log, st, item, wt.path) if ours else PT.manual(repo, cfg, st, item)
        )
    return O.ok(
        "item.claimed",
        port=port,
        port_advice=PT.advice(port, item) if port else "",
        item=item,
        holder=lz.holder,
        worktree=str(wt.path) if wt else "",
        branch=wt.branch if wt else "",
        adopted=was_adopted if rebound else bool(wt and not wt.created and not ours),
        rebound=rebound,
        # Whether the caller already stands in the tree it was given: a rebound tree
        # is usually somewhere else, and the caller has to be told to go there.
        here=bool(wt and _tree_of(called_from or repo) == Path(wt.path).resolve()),
        ttl_s=cfg.lease.ttl_s,
        heartbeat_s=cfg.lease.heartbeat_s,
        base=wt.base if wt else "",
    )


def heartbeat(
    repo: Path, item: str, *, agent: str = "", called_from: Path | None = None
) -> O.Outcome:
    """Renew a lease: one I hold, or the one that made the tree I am standing in.

    Identity is derived from the tree, so an agent working in the tree `claim` made for
    an item is usually NOT the identity that claimed it -- and was told "no lease held"
    by the one command it runs from where it works. `check_commit` already counts the
    lease that created this tree as mine; so does this. It renews on behalf of the
    holder, never taking the lease over, and standing in some OTHER tree renews nothing.
    """
    log, cfg, st = _load(repo, agent)
    renewed = L.renew(log, item)
    hint = ""
    if not renewed:
        it = st.items.get(item)
        lease = it.lease if it else None
        if lease and _in_leased_tree(repo, lease.worktree, _tree_of(called_from or repo)):
            # Only a LIVE lease. Speaking for an expired one would resurrect a claim its
            # holder abandoned, and lock out whoever `recover` sent to take it over.
            if lease.expired_at or lease.expired(time.time(), cfg.lease.grace_s):
                hint = f"; its lease (held by {lease.holder}) has expired -- `claim {item}` again"
            else:
                renewed = L.renew(log, item, holder=lease.holder)
    if renewed:
        withheld = _catch_up_globs(log, cfg, item)
        return O.ok(
            "lease.renewed",
            id=item,
            renewed=True,
            waiters=_waiters(repo, item),
            globs_withheld=withheld,
        )
    return O.nothing(
        "lease.renewed",
        f"no lease held {item}{hint}",
        id=item,
        renewed=False,
        waiters=[],
        globs_withheld="",
    )


def _catch_up_globs(log, cfg, item: str) -> str:
    """Point a just-renewed lease at the item's CURRENT globs; "" or why it could not.

    `update --globs` retargets a LIVE lease and leaves a lapsed one for recovery, so an
    edit made while the lease had lapsed reached only the item. The heartbeat that then
    revived the lease kept its claim-time globs, and the commit hook reported the paths
    just added as unleased (Bb21d338f26). Now that the lease is live again it takes the
    item's globs -- through `plan_retarget`, the same overlap check as any widening, so
    paths another agent took in the meantime are withheld and the holder is told.
    """
    from ..core.model import fold

    with log.transaction():
        it = fold(log.read_all(), strict=False).items.get(item)
        lease = it.lease if it else None
        # Globs cleared to none are caught up too: a revived lease left on the old paths
        # would keep holding them against every other agent.
        if lease is None or sorted(lease.globs) == sorted(it.globs):
            return ""
        live, refusal = L.plan_retarget(log, cfg, item, list(it.globs))
        if refusal:
            return refusal
        if live is not None:
            L.retarget(log, item, live, list(it.globs))
    return ""


def _waiters(repo: Path, item: str) -> list[dict[str, Any]]:
    """Who is blocked on `item` right now, from `ddflow wait` registrations.

    The holder's side of waking: at a heartbeat it is a reason to finish, narrow the
    globs, or release early; at a release or completion it is the list of agents this
    just woke. Advisory -- a registry that cannot be read is simply no waiters.
    """
    from ..services import waits as WT

    try:
        return WT.waiting_on(repo, item)
    except (OSError, ValueError, TypeError, AttributeError):
        return []  # advisory: a waiter's bad file must never stop a holder releasing


def release(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    log, _cfg, _st = _load(repo, agent)
    # Read BEFORE letting go: a waiter wakes on the release and unregisters, and one
    # quick enough to do that before a read after it would never be reported.
    waiting = _waiters(repo, item)
    released = L.release(log, item, note=note)
    if released:
        return O.ok("lease.released", id=item, released=True, woke=waiting)
    return O.nothing("lease.released", f"no lease on {item}", id=item, released=False, woke=[])


def _session_model(st: State, agent: str) -> str:
    """The model `agent` declared on its most recent OPEN session, or "".

    Only this agent's: another agent's session names another author, and borrowing its
    model would judge reviewer independence against the wrong family.
    """
    open_ = [s for s in st.sessions.values() if s.agent == agent and s.model and not s.ended_at]
    return max(open_, key=lambda s: s.started_at).model if open_ else ""


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

    # The author is whoever completes; the model it declared at `session start` is its
    # model unless it says otherwise here (B7a5c63e3d2). Both surfaces arrive here, so
    # CLI and MCP default alike -- MCP's `clientInfo` names the harness, not a model.
    model = model or _session_model(st, log.agent_id)
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
    waiting = _waiters(repo, item)  # before the release: see `release`
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
    return O.ok("item.completed", forced=forced, woke=waiting, **base)


def _abandon_refused(item: str, reason: str, why: str) -> O.Outcome:
    """Built directly: `O.refused(kind, reason, **data)` owns `reason`, so passing the
    abandon reason as a wire field raised TypeError -- `abandon` on a DONE item crashed
    with exit 1 instead of refusing with exit 3, and the refusal text was never shown."""
    return O.Outcome(
        kind="item.abandoned", data={"id": item, "reason": reason}, exit=O.REFUSED, reason=why
    )


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
    if it.state == REVIEW and it.pr and it.pr.state == "open" and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} has an open request ({it.pr.url}). Abandoning it here leaves that "
            f"request open -- mergeable by anyone, recorded by no one -- and anything "
            f"stacked on it would be re-based onto the target carrying its commits. Close "
            f"the request (`pr sync` then parks it), or --force.",
        )
    if it.state == DONE and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} is already done; abandoning it would rewrite finished history. "
            f"--force if you really mean it.",
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


def unblock(repo: Path, item: str, *, note: str = "", agent: str = "") -> O.Outcome:
    """Release a blocked item -- and every blocked item beneath it -- back into the queue.

    The subtree is what makes "drive this legacy section" one command. An import holds
    the open work of an archive file as blocked, one phase per section, and naming the
    section is how its work becomes work again (the source project's `phase.py next
    --session X`). Releasing thirty tasks one id at a time is how half of them stay held.

    Exit 2 when nothing under `item` is blocked: "nothing to release" is a fact the
    caller should see, not a success that wrote no event.
    """
    from ..core.model import BLOCKED

    log, _cfg, st = _load(repo, agent)
    it = _require(st, item, "item.unblocked")
    if isinstance(it, O.Outcome):
        return it
    targets = [item] if it.state == BLOCKED else []
    targets += sorted(
        d for d in st.descendants(item) if st.items[d].state == BLOCKED and not st.items[d].removed
    )
    if not targets:
        return O.nothing(
            "item.unblocked",
            f"{item} is {it.state} and nothing beneath it is blocked",
            id=item,
            was="",
            released=[],
        )
    was = it.blocked_reason if it.state == BLOCKED else ""
    for t in targets:
        log.append("item.unblocked", t, {"note": note, "was": st.items[t].blocked_reason})
    return O.ok("item.unblocked", id=item, was=was, released=targets)


def merge(
    repo: Path,
    item: str,
    *,
    message: str = "",
    allow_dirty: bool = False,
    keep: bool = False,
    model: str = "",
    branch: str = "",
    called_from: Path | None = None,
    agent: str = "",
) -> O.Outcome:
    """Land an item's branch. The most consequential action in the package.

    With `[flow].integration = "pr"` landing is a person's decision, so this opens (or
    updates) the request instead and parks the item in REVIEW; `pr sync` finishes it.
    Same verb either way, so an agent's loop does not change with the repository's
    merge policy.

    An item claimed WITHOUT a worktree has no branch of its own, so it lands the branch
    it was worked on: ``branch`` when named, else the one checked out in the linked
    worktree the caller stands in (`_branch_to_land`). That tree is borrowed, never
    removed. It used to refuse ("has no worktree to merge"), and the work was then
    landed by hand, outside the log -- no `worktree.merged`, no merge gate.
    """
    from ..core import flow as F
    from ..services import flow as FS
    from ..services import gates as G

    log, cfg, st = _load(repo, agent)
    it = _require(st, item, "worktree.merged")
    if isinstance(it, O.Outcome):
        return it
    target = FS.target(repo, cfg, it, st)
    borrowed = not it.worktree  # claimed --no-worktree: land the branch it was worked on
    source = _what_to_land(repo, cfg, st, it, target, branch, called_from)
    if isinstance(source, O.Outcome):
        return source
    wt, dirty, outside = source
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
    remote_base = f"{cfg.flow.remote}/{wt.base}"
    if it.base and it.base not in (wt.base, remote_base) and FS.stacked_on(st, it) is None:
        # The branch was forked from one line's base and would land on another's,
        # carrying the first line's history along (RESEARCH R17 review).
        return O.refused(
            "worktree.merged",
            f"{item}'s branch was forked from {it.base!r} but would land on {wt.base!r}. "
            f"Merging would carry {it.base}'s history into {wt.base}. Re-file the work on "
            f"the line it was forked for, or port it.",
            id=item,
            sha="",
            dirty=[],
        )
    if cfg.flow.integration == "pr":
        if borrowed:
            return O.refused(
                "worktree.merged",
                f"{item} was claimed without a worktree, and a pull request is opened from "
                f"the item's own branch. Push {wt.branch!r} and open the request yourself, "
                f"or claim {item} with a worktree.",
                id=item,
                sha="",
                dirty=[],
            )
        return _open_request(repo, cfg, log, it, message=message, model=model, dirty=dirty)
    bad = F.problems(cfg)
    if bad:
        return O.refused("worktree.merged", "; ".join(bad), id=item, sha="", dirty=[])
    sha = W.rev(repo, wt.branch) if borrowed else W.head_sha(wt.path)
    landed_before = W.rev(repo, wt.base)
    r = W.merge(repo, cfg, wt, message=message or f"merge {item}: {it.title}")
    if not r.ok:
        out = O.Outcome(
            kind="worktree.merged",
            data={"id": item, "sha": "", "dirty": []},
            exit=O.REFUSED if r.code == W.GIT_REFUSED else O.FAIL,
            reason=r.err or r.out,
        )
        return out
    log.append(
        "worktree.merged",
        item,
        {
            "sha": sha,
            "branch": wt.branch,
            # The target's range this merge added -- what a cherry-pick port re-applies.
            "landed_before": landed_before,
            "landed_after": W.rev(repo, wt.base),
            # Landed from a branch the item does not own (claimed --no-worktree).
            **({"borrowed": True, "outside_globs": outside} if borrowed else {}),
        },
    )
    # A gitflow hotfix lands on production AND develop. A failure here is reported, not
    # rolled back: production has the fix, which was the urgent half.
    back_merged, back_failed = [], []
    for extra in F.back_merge_targets(it, cfg, W.default_branch(repo), F.effective_line(st, it)):
        br = W.merge_into(repo, cfg, extra, wt.branch, message=f"back-merge {item} into {extra}")
        (back_merged if br.ok else back_failed).append(
            extra if br.ok else f"{extra}: {br.err or br.out}"
        )
    G.record(
        log,
        cfg,
        item,
        "merge",
        "passed",
        evidence={"sha": sha, "branch": wt.branch},
        gates=G.load_gates(repo, cfg),
    )
    # NEVER remove an ADOPTED tree -- see the module docstring. Nor a borrowed one: it
    # is the caller's, or whoever's has that branch checked out.
    kept_reason = ""
    removed_tree = False
    if borrowed:
        kept_reason = f"no worktree of its own: {wt.branch!r} was landed, no tree touched."
    elif it.adopted:
        kept_reason = f"worktree {wt.path} kept: adopted, not created by ddflow."
    elif cfg.worktree.remove_on_merge and not keep:
        rr = W.remove(repo, cfg, wt)
        if rr.ok:
            log.append("worktree.removed", item, {"path": str(wt.path)})
            removed_tree = True
        else:
            kept_reason = rr.err
    if back_failed:
        kept_reason = "; ".join(
            filter(None, [kept_reason, "back-merge FAILED into " + ", ".join(back_failed)])
        )
    return O.ok(
        "worktree.merged",
        id=item,
        sha=sha,
        base=wt.base,
        dirty=list(dirty),
        worktree_removed=removed_tree,
        kept_reason=kept_reason,
        back_merged=back_merged,
        pr="",
        branch=wt.branch,
        outside_globs=outside,
    )


def _what_to_land(
    repo: Path, cfg, st, it, target: str, branch: str, called_from: Path | None
) -> tuple[W.Worktree, list[str], list[str]] | O.Outcome:
    """(the tree and branch to land, its uncommitted files, paths outside the globs).

    An item's own worktree lands its own branch, and naming another is refused. An item
    claimed without one lands a borrowed branch (`_branch_to_land`); its tree, if the
    branch is checked out anywhere, is only inspected for uncommitted work.
    """
    if it.worktree:
        if branch and branch != it.branch:
            return O.refused(
                "worktree.merged",
                f"{it.id} has its own worktree on {it.branch!r}; it lands that branch, not "
                f"{branch!r}. Drop --branch, or land {branch!r} under the item it belongs to.",
                id=it.id,
                sha="",
                dirty=[],
            )
        wt = W.Worktree(
            item=it.id, path=W.load_path(repo, it.worktree), branch=it.branch, base=target
        )
        return wt, W.dirty(wt), []
    picked = _branch_to_land(repo, cfg, st, it, branch, called_from, target)
    if isinstance(picked, O.Outcome):
        return picked
    tree = W.checked_out_at(repo, picked)
    wt = W.Worktree(item=it.id, path=tree or W.repo_root(repo), branch=picked, base=target)
    dirty = W.dirty(wt) if tree is not None else []
    return wt, dirty, _outside_globs(repo, it, target, picked)


def _branch_to_land(
    repo: Path, cfg, st, it, branch: str, called_from: Path | None, target: str
) -> str | O.Outcome:
    """The branch an item claimed WITHOUT a worktree is landed from, or why none can be.

    Named with ``branch``, else the branch checked out in the linked worktree the caller
    stands in -- where an agent whose harness gave it a tree is working. The primary
    names nothing: it is usually on the target itself, and a guess there would land
    whatever it happens to be on.
    """
    if not branch:
        here, held = callers_tree(repo, cfg, st, it, called_from)
        if held:
            return O.refused(
                "worktree.merged",
                f"the worktree you are in belongs to {held}, not {it.id}: its branch is "
                f"{held}'s work. Name {it.id}'s branch with --branch.",
                id=it.id,
                sha="",
                dirty=[],
            )
        if here is None or not here.branch:
            return O.refused(
                "worktree.merged",
                f"{it.id} was claimed without a worktree, so it has no branch of its own. "
                f"Name the branch that holds its commits -- `ddflow merge {it.id} "
                f"--branch <branch>` -- or run merge from the worktree it was worked in.",
                id=it.id,
                sha="",
                dirty=[],
            )
        branch = here.branch
    if not W.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok:
        return O.refused(
            "worktree.merged",
            f"no local branch {branch!r} to land {it.id} from.",
            id=it.id,
            sha="",
            dirty=[],
        )
    if branch == target:
        return O.refused(
            "worktree.merged",
            f"{branch!r} is the merge target itself. Name the branch {it.id} was worked "
            f"on with --branch.",
            id=it.id,
            sha="",
            dirty=[],
        )
    ahead = W.git(repo, "rev-list", "--count", f"{target}..{branch}")
    if ahead.ok and ahead.out.strip() == "0":
        return O.nothing(
            "worktree.merged",
            f"{branch!r} has nothing {target!r} lacks: nothing to merge for {it.id}.",
            id=it.id,
            sha="",
            dirty=[],
        )
    return branch


def _outside_globs(repo: Path, it, target: str, branch: str) -> list[str]:
    """Paths the landing changes that the item never declared.

    A borrowed branch can carry more than this item's work -- another item's commits made
    in the same tree -- and landing that should at least not be silent. Reported, not
    refused: an item's globs are often narrower than its honest diff, and the caller named
    or stood on this branch. ddflow's own bookkeeping paths are not the item's to declare.
    """
    from ..core.schedule import globs_overlap
    from ..services.enforce import SELF_MANAGED

    d = W.git(repo, "diff", "--name-only", f"{target}...{branch}")
    changed = [p for p in d.out.splitlines() if p.strip()] if d.ok else []
    return [
        p
        for p in changed
        if not p.startswith(SELF_MANAGED) and not any(globs_overlap(p, g) for g in it.globs)
    ]


def _open_request(
    repo: Path, cfg, log, it, *, message: str, model: str, dirty: list[str]
) -> O.Outcome:
    from ..services import flow as FS

    op = FS.open_request(repo, cfg, log, it.id, title=message, model=model)
    data: dict[str, Any] = {
        "id": it.id,
        "sha": "",
        "base": op.base,
        "dirty": list(dirty),
        "pr": op.url,
        "number": op.number,
        "stacked_on": op.stacked_on,
        "created": op.created,
        "warnings": op.warnings,
        "review": op.ok,
    }
    if op.unavailable:
        return O.nothing("worktree.merged", op.reason, **data)
    if op.refused:
        return O.refused("worktree.merged", op.reason, **data)
    return O.ok("worktree.merged", **data)


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
    # What THIS agent holds comes before what anyone may take (B226d8db6e8): the top
    # ready item was headed "Current" for an agent that had just claimed another one --
    # it is the queue's pick, not the agent's work. Most recent claim first.
    held = sorted(
        (
            (lease.acquired_at, iid)
            for iid, lease in st.active_leases(time.time(), cfg.lease.grace_s).items()
            if lease.holder == log.agent_id and not st.items[iid].removed
        ),
        reverse=True,
    )
    held_ids = [iid for _, iid in held]
    suggested = False
    if not item and held_ids:
        item = held_ids[0]
    elif not item and p.ready:
        item = p.ready[0].id
        suggested = True

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

    live = sorted(
        (m for m in st.memories.values() if m.live),
        key=lambda m: (m.origin_at or m.at, m.at),
        reverse=True,
    )
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
        memories=live,
        held=held_ids,
        suggested=suggested,
    )
    from ..services import choices as CH

    undecided = CH.brief_block(cfg)
    if undecided:
        text = undecided + "\n" + text
    if item and item in st.items:
        pr = st.items[item].pr
        if pr is not None and pr.review == "changes_requested" and pr.feedback:
            # FIRST, not appended: this is why the item is back, and an agent that fixes
            # something other than what the reviewer asked for starts another round.
            text = (
                f"## Review feedback on {item} (round {pr.rounds}, {pr.url})\n\n"
                f"Address this, commit, then `ddflow merge {item}` again -- it updates the "
                f"same request.\n\n{pr.feedback}\n\n" + text
            )
    return O.ok(
        "brief",
        brief=text,
        text=text,
        item=item,
        #: Whether `item` is the agent's work (False) or only the queue's pick (True).
        suggested=suggested,
        held=held_ids,
        ready=[i.id for i in p.ready],
        approx_tokens=len(text) // 4,
    )
