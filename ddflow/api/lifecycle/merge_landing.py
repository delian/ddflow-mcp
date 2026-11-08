"""The landing and recording helpers behind `merge` (Bf157c33ba2).

Split out of `merge.py` so no one area module grows back into a monolith:
`merge.py` keeps the public `merge()`, the PR/branch path and the test-patched
recording names; this module holds the landing refusals and the shared helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...infra import worktree as W
from ...services import eventcommit as EC
from ...services import flow as FS
from ...services import gates as G
from ...services import leases as LS
from ...services import merge_force as MF
from ...services import pins_dirt as PD
from ...services import pins_merge as PM
from ...services.cleanup import dispose_tree, stands_in
from ...services.gates import failed_gate as OC
from ...services.gates import failed_review as GO
from ...services.gates import measured as GM


def _say_pins(repo: Path, cfg: Any, say: Any) -> None:
    """Say which pins the merge left stale or never re-recorded."""
    for line in PM.merge_lines(PM.merge_report(repo, cfg)):
        say(line)
def _release_at_merge(log, cfg, item: str, back_failed: list[str]) -> bool:
    """End the item's lease now that its merge is recorded (LK-release-at-merge): the files
    are free for a waiter, whose `wait` reads the release as ready, and `complete` needs no
    lease. Kept when `[lease].release_at_merge` is off or a hotfix back-merge failed (it needs
    its files to retry). Only reached after a landing, so a failed merge keeps the lease."""
    return not back_failed and cfg.lease.release_at_merge and LS.release(log, item, note="merged")
def _wrong_base(st, it, wt, remote_base: str, item: str) -> O.Outcome | None:
    """A refusal when the branch was forked from one line's base and would land on another's,
    carrying the first line's history along (RESEARCH R17 review)."""
    if it.base and it.base not in (wt.base, remote_base) and FS.stacked_on(st, it) is None:
        return O.refused(
            "worktree.merged",
            f"{item}'s branch was forked from {it.base!r} but would land on {wt.base!r}. "
            f"Merging would carry {it.base}'s history into {wt.base}. Re-file the work on "
            f"the line it was forked for, or port it.",
            id=item,
            sha="",
            dirty=[],
        )
    return None
def _when(cond: Any, **fields: Any) -> dict[str, Any]:
    """``fields`` when ``cond`` is truthy, else nothing: an optional part of a result."""
    return fields if cond else {}


def _kept_reason(kept: str, landed_note: str, back_failed: list[str]) -> str:
    """Why the tree was kept and what went wrong after the landing, as one line."""
    failed = ("back-merge FAILED into " + ", ".join(back_failed)) if back_failed else ""
    return "; ".join(filter(None, [kept, landed_note, failed]))
def _say_awaiting(say, item: str, gates) -> None:
    """Say which required gates the landing left unsettled (`[merge].require_gates`)."""
    if gates.unmet:
        say(MF.warning(item, gates))
def _ci_fields(ci_after: dict) -> dict:
    """`ci` is kept for readers of the old key; it is the BASE's post-merge check, never the ci
    gate (Bb55340e14b): `gate_states` has the gate."""
    return {"ci": ci_after, "post_merge_check": ci_after} if ci_after else {}
def _commit_shards_before_merge(log, cfg, item: str) -> None:
    """Commit the primary's uncommitted event shards before the git merge, unless the
    strategy is a fast-forward: landing them on the target drifts it past the branch's fork
    and makes ``git merge --ff-only`` report diverged branches instead of landing
    (B83b4102c08). For ff-only the shards are committed after the landing, on the
    fast-forwarded target, by the post-merge `commit_at` in `merge`."""
    if cfg.worktree.merge_strategy != "ff-only":
        EC.commit_at(log, cfg, "merge", f"merge {item}")
def _gate_blockers(repo: Path, cfg, st, item: str) -> str:
    """The first failed-gate blocker that stops ``item`` landing, or ""."""
    found = GO.failed_review_blockers(st, cfg, item, G.load_gates(repo, cfg))
    return (found or OC.failed_ci_blockers(st, cfg, item) or [""])[0]
def _landing_refusal(
    repo: Path, cfg, st, it, wt: W.Worktree, dirty, item: str, allow_dirty: bool, allow_empty: bool
) -> O.Outcome | None:
    """None when the tree and branch can land; else the refusal that stops the landing.

    The four refusals every landing must pass before git acts: an unreadable tree, an
    uncommitted one (without --allow-dirty), a branch with nothing to land (without
    --allow-empty), and a branch forked from the wrong base.
    """
    if W.unreadable(dirty):
        # Not a file list: git could not read the tree at all. Commit-or---allow-dirty is
        # the wrong advice, and --allow-dirty must not land a tree nobody can see
        # (Bb2f7566528). `dirty` stays a list of paths.
        return O.refused(
            "worktree.merged",
            f"could not read the worktree {wt.path}: {dirty[0][len(W.UNREADABLE) :]}. "
            f"Repair it (its .git file, permissions) or `ddflow recover`; nothing was "
            f"merged.",
            id=item,
            dirty=[],
            # Unknown, not clean: as `outside_globs_unknown`, so a caller reading `dirty`
            # alone never takes an unread tree for an empty one.
            dirty_unknown=True,
            path=str(wt.path),
        )
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
    empty = None if allow_empty else _lands_nothing(repo, it, wt)
    if empty is not None:
        return empty
    return _wrong_base(st, it, wt, f"{cfg.flow.remote}/{wt.base}", item)
def _record_landing(
    log,
    item: str,
    wt,
    gates,
    *,
    borrowed: bool,
    outside,
    sha: str,
    branch_head: str,
    landed_before: str,
    landed_after: str,
) -> None:
    """Record `worktree.merged`: what the base points at now, and each gate's state at landing."""
    log.append(
        "worktree.merged",
        item,
        {
            "sha": sha,
            "branch_head": branch_head,
            "branch": wt.branch,
            # The target's range this merge added -- what a cherry-pick port re-applies.
            "landed_before": landed_before,
            "landed_after": landed_after,
            # Each gate's real state when it landed (Bb55340e14b), not the post-merge check's.
            "gates": gates.states,
            # Landed from a branch the item does not own (claimed --no-worktree).
            **({"borrowed": True, **_scope_fields(outside, listed=True)} if borrowed else {}),
        },
    )
def _failed_landing_outcome(
    repo: Path, cfg, log, it, wt, item: str, landed_note: str, r, *, borrowed: bool
) -> O.Outcome | None:
    """The outcome when git did not land and no note says it did; None when it landed.

    A merge that was tried and failed is a merge-gate outcome: the log-derived
    `merge_failure_rate` flow signal counts it, and without this it saw successes only.
    """
    if r.ok or landed_note:
        return None
    if r.attempted:  # a refused precondition says nothing about this branch
        GM.record_merge(
            log,
            cfg,
            repo,
            it,
            "failed",
            gates=G.load_gates(repo, cfg),
            tree=None if borrowed else wt.path,
            reason=(r.err or r.out or "merge failed")[:500],
            evidence={"branch": wt.branch, "git_exit": r.code},
        )
    return O.Outcome(
        kind="worktree.merged",
        data={"id": item, "sha": "", "dirty": []},
        exit=O.REFUSED if r.code == W.GIT_REFUSED else O.FAIL,
        reason=(r.err or r.out) + PD.dirt_hint(repo, cfg, r.err or r.out, repo),
    )
def _lands_nothing(repo: Path, it, wt: W.Worktree) -> O.Outcome | None:
    """A refusal when ``wt.branch`` has no commits its target lacks; else None.

    Landing it would record the item merged while nothing reached the target -- which
    is what an item bound to the WRONG tree did: its bound branch was empty, the work
    sat on another branch, and `merge` exited 0 (Bec8d5228c9, D-sticky-binding-remedy).
    Not refused when git cannot answer (`W.is_merged` is then False): that is for
    `W.merge` to report. ``wt.branch`` is the branch that would land: an item with a tree
    of its own refuses `--branch` earlier (`_what_to_land`), and a borrowed branch IS
    the one named, already asked the same question by `_branch_to_land`.
    """
    if not W.is_merged(repo, wt.branch, wt.base):
        return None
    return O.refused(
        "worktree.merged",
        f"{wt.branch!r} has no commits ahead of {wt.base!r}: merging it would land "
        f"nothing and record {it.id} merged. If {it.id}'s work is on another tree, bind "
        f"the item to it -- `ddflow update {it.id} --worktree <path>` -- and merge again; "
        f"if there is truly nothing to land, pass --allow-empty.",
        id=it.id,
        sha="",
        dirty=[],
        branch=wt.branch,
    )
def _dispose_tree(
    repo: Path, cfg, log, it, wt: W.Worktree, *, borrowed: bool, keep: bool, callers
) -> tuple[bool, str]:
    """(removed, why it was kept) for a landed item's tree.

    NEVER remove an ADOPTED tree -- see the module docstring. Nor a borrowed one: it is
    the caller's, or whoever's has that branch checked out. Nor the one the caller stands
    in: its shell would be left in a deleted directory (B1172da8c35).
    """
    if borrowed:
        return False, f"no worktree of its own: {wt.branch!r} was landed, no tree touched."
    if it.adopted:
        return False, f"worktree {wt.path} kept: adopted, not created by ddflow."
    if not cfg.worktree.remove_on_merge or keep:
        return False, ""
    if stands_in(wt.path, *callers):
        return False, (
            f"worktree {wt.path} kept: you are standing in it, and removing it would leave "
            f"your shell in a deleted directory. cd {W.repo_root(repo)} -- `ddflow cleanup "
            f"--apply` removes the tree once {it.id} is complete."
        )
    gone = dispose_tree(repo, cfg, log, wt, item=it)
    return (True, "") if gone.removed else (False, gone.why)
def _scope_fields(outside: list[str] | None, *, listed: bool) -> dict[str, Any]:
    """`outside_globs` as recorded and returned: a list always, and `outside_globs_unknown`
    beside it -- true when git could not list the landing's paths, false when it did, null
    when nothing was listed (``listed=False``: an item landing its own worktree, whose globs
    are its lease) -- so an empty list is never read as a clean scope that nobody checked
    (B4e42502034)."""
    if not listed:
        return {"outside_globs": [], "outside_globs_unknown": None}
    if outside is None:
        return {"outside_globs": [], "outside_globs_unknown": True}
    return {"outside_globs": outside, "outside_globs_unknown": False}
