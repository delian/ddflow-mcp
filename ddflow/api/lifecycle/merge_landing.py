"""The landing and recording helpers behind `merge` (Bf157c33ba2).

Split out of `merge.py` so no one area module grows back into a monolith:
`merge.py` keeps the public `merge()` plus the PR/branch path; this module holds the
landing refusals, the gate assessment/authorisation recording and the landing's
event recording."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import flow as F
from ...core import outcome as O
from ...infra import worktree as W
from ...services import ci as CI
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
from ..ci import check_after_merge


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

def _landed_note(repo: Path, wt, landed_before: str, branch_head: str, r) -> str:
    """ "" for a clean merge; else the note `_landed_anyway` has for a failure that landed."""
    return "" if r.ok else _landed_anyway(repo, wt, landed_before, branch_head, r)

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

def _record_forced(log, item: str, sha: str, gates, stamp, agent: str) -> None:
    """The `merge.forced` event of a landing that went over unsettled gates (no-op otherwise)."""
    if stamp is not None:
        log.append(
            "merge.forced",
            item,
            {"sha": sha, "unmet_gates": gates.awaiting(), "approval": stamp, "agent": agent},
        )

def _spend_after_land(log, cfg, item: str, gates, stamp: dict | None, pending: str) -> dict | None:
    """Spend an agent's approval only now the landing landed (B527b0a518c). ``pending`` names
    the approval id to spend, and is "" unless `authorise` validated a real `--approval <id>`;
    a person's `--force` and a standing-policy stamp pass through untouched. A spend that fails
    after a concurrent consumer (or expiry between validation and spend) returns a STRUCTURED
    marker (`spend: "failed"`) -- never a valid-looking proof, and never a silent drop."""
    if not pending:
        return stamp
    spent = MF.spend(log, cfg, item, gates, pending)
    return spent.stamp if spent.ok else {"ask": pending, "spend": "failed", "note": spent.message}

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

def _finish_merge(  # noqa: PLR0913 -- the landing already cost each of these; the tail spends them
    repo: Path,
    cfg,
    log,
    st,
    it,
    wt,
    item: str,
    gates,
    forced_by,
    pending_approval: str,
    *,
    agent: str,
    borrowed: bool,
    keep: bool,
    outside,
    dirty,
    refreshed,
    sha: str,
    branch_head: str,
    landed_note: str,
    merge_since: str,
    callers,
    on_progress,
) -> O.Outcome:
    """Everything after the landing is recorded: spend the approval, back-merge a hotfix,
    record the merge gate, run the post-merge check and commit the events."""
    # Spend an agent's approval only now, once the landing landed: a person's `--force` and
    # a standing-policy answer have no approval to spend (pending_approval == ""), and a
    # failed landing returned above before reaching here (B527b0a518c).
    forced_by = _spend_after_land(log, cfg, item, gates, forced_by, pending_approval)
    _record_forced(log, item, sha, gates, forced_by, agent)
    # A gitflow hotfix lands on production AND develop. A failure here is reported, not
    # rolled back: production has the fix, which was the urgent half.
    back_merged, back_failed = [], []
    for extra in F.back_merge_targets(it, cfg, W.default_branch(repo), F.effective_line(st, it)):
        br = W.merge_into(repo, cfg, extra, wt.branch, message=f"back-merge {item} into {extra}")
        (back_merged if br.ok else back_failed).append(
            extra if br.ok else f"{extra}: {br.err or br.out}"
        )
    # After the back-merges: a hotfix whose back-merge failed still needs its files to retry.
    freed = _release_at_merge(log, cfg, item, back_failed)
    human_gate = GM.record_merge(
        log,
        cfg,
        repo,
        it,
        "passed",
        gates=G.load_gates(repo, cfg),
        tree=None if borrowed else wt.path,
        reason=landed_note,
        evidence={"sha": sha, "branch_head": branch_head, "branch": wt.branch},
    )
    removed_tree, kept_reason = _dispose_tree(
        repo, cfg, log, it, wt, borrowed=borrowed, keep=keep, callers=callers
    )
    kept_reason = _kept_reason(kept_reason, landed_note, back_failed)

    say = on_progress or (lambda _line: None)
    say(f"merged {item} into {wt.base} ({sha[:12]}); the merge is done and recorded")
    _say_awaiting(say, item, gates)
    say(_ci_notice(repo, cfg))
    ci_after = check_after_merge(repo, sha=sha, item=item, agent=agent, since=merge_since)
    say(
        f"post-merge check finished ({ci_after.get('status', 'nothing to check')}); committing the event log"
    )
    _say_pins(repo, cfg, say)
    # Last: the after-merge check may record a result of its own.
    committed = EC.commit_at(log, cfg, "merge", f"merge {item}")
    return O.ok(
        "worktree.merged",
        id=item,
        **_ci_fields(ci_after),
        gate_states=gates.states,
        awaiting_gates=gates.awaiting(),
        forced=forced_by is not None,
        sha=sha,
        branch_head=branch_head,
        base=wt.base,
        dirty=list(dirty),
        worktree=str(wt.path),
        worktree_removed=removed_tree,
        kept_reason=kept_reason,
        back_merged=back_merged,
        pr="",
        branch=wt.branch,
        **_scope_fields(outside, listed=borrowed),
        # The merge gate is a person's to clear here; nothing was recorded for it.
        merge_gate_human=human_gate,
        lease_released=freed,
        **_when(refreshed, export_refresh=refreshed),
        **committed,
    )

def _ci_notice(repo: Path, cfg) -> str:
    """The line said before the post-merge check starts: what runs and which knobs bound it."""
    try:
        command, why = CI.main_command(repo, cfg)
        notice = (
            f"post-merge check starting: [ci].on_merge={cfg.ci.on_merge}, at most "
            f"{cfg.ci.timeout_s}s ([ci].timeout_s); a timeout is recorded unavailable, never "
            f"as a failure"
        )
    except Exception:  # a line of progress never stops a merge that has landed
        return "post-merge check starting"
    if not command:
        return f"post-merge check: nothing to run ({why or 'no CI command in this project'})"
    return notice

def _landed_anyway(repo: Path, wt: W.Worktree, landed_before: str, branch_head: str, r) -> str:
    """A note when git reported failure but the merge commit is on the base; else "".

    git can be killed at its timeout AFTER it committed: the post-merge hook, which runs
    inside `git merge`, takes minutes under load. Reporting that as a refusal left the
    merge gate failed for a branch the base already held, and the next `merge` said "no
    commits ahead" (B04bab3d1b3). The base having moved to a head that contains the branch
    is what happened, whatever git's exit status said.
    """
    if not r.attempted:
        return ""
    now = W.rev(repo, wt.base)
    # The head this run tried to land, not the branch ref: an item's tree can be ahead of it.
    contained = (
        bool(branch_head) and W.git(repo, "merge-base", "--is-ancestor", branch_head, wt.base).ok
    )
    if not now or now == landed_before or not contained:
        return ""
    how = "timed out" if r.timed_out else f"exited {r.code}"
    return (
        f"git {how} after the merge commit landed on {wt.base} ({now[:12]}): recorded as "
        f"merged, from what the base holds ({(r.err or r.out or '')[:200]!r})"
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

