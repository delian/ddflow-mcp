"""`merge`: landing an item's branch, and what it refreshes after.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import flow as F
from ...core import outcome as O
from ...core.admission import new_file_globs
from ...core.schedule import path_in_glob
from ...infra import worktree as W
from ...services import changes as CH
from ...services import ci as CI
from ...services import eventcommit as EC
from ...services import flow as FS
from ...services import gates as G
from ...services import identity as ID
from ...services import leases as LS
from ...services import merge_force as MF
from ...services.cleanup import (  # noqa: F401  (re-exported)
    dispose_tree,
    record_item_removed,
)
from ...services.enforce import SELF_MANAGED
from ...services.gates import measured as GM
from .._base import _load
from ..ci import check_after_merge
from ._common import _require
from .claim import callers_tree
from .merge_landing import (
    _ci_fields,
    _commit_shards_before_merge,
    _dispose_tree,
    _failed_landing_outcome,
    _gate_blockers,
    _kept_reason,
    _landing_refusal,
    _record_landing,
    _release_at_merge,
    _say_awaiting,
    _say_pins,
    _scope_fields,
    _when,
)
from .merge_refresh import _refresh_documents


def _override(repo: Path, cfg, log, st, item: str, force: bool, approval: str, agent: str):
    """(assessment, stamp, pending, refusal): a configuration problem refuses first, then what
    `[merge].require_gates` makes of the unsettled gates.
    An agent's approval is only VALIDATED here (`MF.authorise` checks it without spending);
    ``pending`` names the id to spend via `MF.spend` once the git landing has landed, so a
    landing that fails on a conflict or hook does not consume a single-use approval
    (B527b0a518c). "" for a person's force and for a standing-policy answer (already consumed)."""
    bad = F.problems(cfg)
    if bad:
        return (
            None,
            None,
            "",
            O.refused("worktree.merged", "; ".join(bad), id=item, sha="", dirty=[]),
        )
    gates = MF.assess(repo, cfg, st, item)
    if not gates.refuses:
        return gates, None, "", None
    stamp, why, pending = MF.authorise(
        log, cfg, item, gates, force=force, approval=approval, agent=agent
    )
    if not why:
        return gates, stamp, pending, None
    return (
        gates,
        None,
        "",
        O.refused("worktree.merged", why, id=item, sha="", dirty=[], unmet_gates=list(gates.unmet)),
    )


def _landed_note(repo: Path, wt, landed_before: str, branch_head: str, r) -> str:
    """ "" for a clean merge; else the note `_landed_anyway` has for a failure that landed."""
    return "" if r.ok else _landed_anyway(repo, wt, landed_before, branch_head, r)


def _record_forced(log, item: str, sha: str, gates, stamp, agent: str) -> None:
    """The `merge.forced` event of a landing that went over unsettled gates (no-op otherwise)."""
    if stamp is not None:
        log.append(
            "merge.forced",
            item,
            {"sha": sha, "unmet_gates": gates.awaiting(), "approval": stamp, "agent": agent},
        )


def _record_reconciled(
    repo, cfg, log, st, it, item: str, landed_before: str, landed_after: str, branch_head: str
) -> None:
    """Record `merge.reconciled` when this landing touched a file another item changed since
    this item forked: the reconcile-at-merge safety net of D-lease-hold-limit-r2. Complete is
    then refused until the affected tests pass on the reconciled tree, unless
    `[lease].reconcile_tests = off`. A clean landing over nothing records nothing."""
    if cfg.lease.reconcile_tests == "off":
        return
    fork = it.base_sha or landed_before
    mine = CH.changed_paths(repo, fork, tip=branch_head, include=("committed",), fork=True)
    if mine is None:
        # git could not tell what this item changed (a lost rev, a shallow tree): fail CLOSED
        # with a reconcile whose empty file list still blocks, so an unreconciled overlap is
        # never silently skipped when the probe cannot answer.
        log.append(
            "merge.reconciled",
            item,
            {"files": [], "base": landed_before, "main": landed_after, "unknown": True},
        )
        return
    if not mine:
        return
    # The overlap trigger is "this landing touched a file `main` changed since this item
    # forked" -- another item merged since the fork, OR a main-into-branch conflict resoved at
    # sync (a direct `main` commit touching a file this branch also touched). Both are the one
    # set `mine ∩ moved`, where `moved` is what `landed_before` did since `fork`; a direct
    # `main` commit is a `moved` file with no `merged_sha`, so it no longer slips past a
    # per-item loop.
    moved = CH.changed_paths(repo, fork, tip=landed_before, include=("committed",), fork=True)
    if moved is None:
        # git could not tell what `main` changed since the fork: fail CLOSED by reconciling
        # over MY files, so an unanswerable probe still holds complete for a reconciled test.
        log.append(
            "merge.reconciled",
            item,
            {"files": sorted(mine), "base": landed_before, "main": landed_after, "unknown": True},
        )
        return
    files = sorted(set(mine) & set(moved))
    if files:
        log.append(
            "merge.reconciled",
            item,
            {"files": files, "base": landed_before, "main": landed_after},
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


def merge(  # noqa: PLR0913 -- each flag is a distinct refusal the caller may override, plus where it stands
    repo: Path,
    item: str,
    *,
    message: str = "",
    allow_dirty: bool = False,
    keep: bool = False,
    model: str = "",
    branch: str = "",
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
    agent: str = "",
    allow_empty: bool = False,
    force: bool = False,
    approval: str = "",
    on_progress=None,
) -> O.Outcome:
    """Land an item's branch. The most consequential action in the package.

    ``sha`` in the result and in `worktree.merged` is the commit the base points at
    after the landing -- the merge commit, or the branch head on a fast-forward -- as
    the PR path records the forge's merge commit. It used to be the branch's head
    (B9f8019c521): a caller citing the merge cited a commit that is not the landing,
    and under a squash strategy is not on the base at all. That head is
    ``branch_head``.

    The item's tree is NOT removed when the caller stands in it (``called_from`` or
    ``shell_cwd``, the CLI process's own directory): deleting a shell's working
    directory makes its next `pwd` fail, and a harness that runs one after every
    command reported the landed merge as a failure (B1172da8c35).

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

    log, cfg, st = _load(repo, agent)
    it = _require(st, item, "worktree.merged", cfg.lease.grace_s, log.clone_id(create=False))
    if isinstance(it, O.Outcome):
        return it
    if (contested := LS.contest_refusal(it, "worktree.merged", "merged")) is not None:
        return contested  # B2809f4213b: before any git action
    held = LS.foreign_live_lease(log, cfg, item, caller=agent)
    if held is not None:
        # Bddda807b51: before any git action, like contest_refusal. The lease it ends at
        # merge is the current holder's, so a non-holder landing it would release the
        # holder's live lease and delete their worktree under a running process.
        who = held.holder
        return O.refused(
            "worktree.merged",
            f"cannot merge {item}: it is leased by {who} and the lease is live; "
            "merging releases their lease and removes their worktree. Ask them, wait "
            "for the lease to lapse, or release it first.",
            id=item,
            sha="",
            dirty=[],
        )
    if ID.is_agent(agent):
        held = LS.peer_live_lease(log, cfg, item, caller=log.agent_id)
        if held is not None:
            # A same-clone peer agent's live lease: refuse like a foreign one (the
            # operator recovering a crashed agent's item is exempt -- not is_agent).
            who = held.holder
            return O.refused(
                "worktree.merged",
                f"cannot merge {item}: it is leased by {who} and the lease is live; "
                "merging releases their lease and removes their worktree. Ask them, "
                "wait for the lease to lapse, or release it first.",
                id=item,
                sha="",
                dirty=[],
            )
    if blocked := _gate_blockers(repo, cfg, st, item):
        # D-failed-review-gate (B85841c0846): landing work whose review failed and was
        # never re-reviewed ships it unreviewed; DX-skip-failed-gate: nor a failed ci.
        # Before any git action.
        return O.refused(
            "worktree.merged", f"cannot merge {item}: {blocked}", id=item, sha="", dirty=[]
        )
    target = FS.target(repo, cfg, it, st)
    borrowed = not it.worktree  # claimed --no-worktree: land the branch it was worked on
    source = _what_to_land(repo, cfg, st, it, target, branch, called_from)
    if isinstance(source, O.Outcome):
        return source
    wt, dirty, outside = source
    if (
        refusal := _landing_refusal(repo, cfg, st, it, wt, dirty, item, allow_dirty, allow_empty)
    ) is not None:
        return refusal
    # The require_gates check runs for EVERY integration mode (Bc6c07dfe52): under `pr` it
    # must still refuse (mode `refuse`) or name the unmet gates (mode `warn`) instead of
    # opening a request past them. An agent's approval is only checked here; the non-PR path
    # spends it after the git landing below actually landed.
    gates, forced_by, pending_approval, over = _override(
        repo, cfg, log, st, item, force, approval, agent
    )
    if over is not None:
        return over
    if cfg.flow.integration == "pr":
        return _open_request_outcome(
            repo,
            cfg,
            log,
            it,
            wt,
            item,
            gates,
            forced_by,
            pending_approval,
            agent,
            borrowed=borrowed,
            message=message,
            model=model,
            dirty=dirty,
        )
    refreshed = {} if borrowed else _refresh_documents(repo, cfg, wt)
    branch_head = W.rev(repo, wt.branch) if borrowed else W.head_sha(wt.path)
    # The primary keeps its event shards uncommitted between doors; a branch that also
    # changes a shard (an older/newer copy of an agent's log) then makes git refuse the
    # merge with 'local changes would be overwritten', leaving the operator to hand-commit
    # a shard first (B6b7e47955e). Commit the shards HERE, before git merges, so they are
    # not the uncommitted work git refuses to overwrite.
    #
    # ``check_after_merge``'s ``since`` is "the base the merge started from" (ddflow/api/ci.py):
    # the target's HEAD before the landing's own shard commit, so the tripwire range is the
    # merge the caller asked for and not every file. Read it BEFORE EC.commit_at below, which
    # also moves the base by landing the event shards (B6b7e47955e); the range a caller saw
    # must not skip that commit and start from a base the caller never held (Be40e9c84d5).
    merge_since = W.rev(repo, wt.base)
    _commit_shards_before_merge(log, cfg, item)
    # Capture the pre-merge base AFTER the shard commit above, when one was made: that
    # commit moves the base (it lands the event shards on the primary), so a landing check
    # against a rev read before it would misread a refused git merge the shard commit caused
    # as "landed anyway". Under ff-only the helper commits nothing, so `landed_before` equals
    # `merge_since`: still the right read for `_landed_anyway`, just with no shard commit stepped.
    landed_before = W.rev(repo, wt.base)
    (on_progress or (lambda _line: None))(f"merging {wt.branch} into {wt.base} ...")
    r = W.merge(repo, cfg, wt, message=message or f"merge {item}: {it.title}")
    landed_note = _landed_note(repo, wt, landed_before, branch_head, r)
    if (
        failed := _failed_landing_outcome(
            repo, cfg, log, it, wt, item, landed_note, r, borrowed=borrowed
        )
    ) is not None:
        return failed
    # What the base now points at: the landing. Falls back to the branch head only if
    # the base cannot be read back, which a merge that just succeeded makes unlikely.
    landed_after = W.rev(repo, wt.base)
    sha = landed_after or branch_head
    _record_landing(
        log,
        item,
        wt,
        gates,
        borrowed=borrowed,
        outside=outside,
        sha=sha,
        branch_head=branch_head,
        landed_before=landed_before,
        landed_after=landed_after,
    )
    _record_reconciled(repo, cfg, log, st, it, item, landed_before, landed_after, branch_head)
    return _finish_merge(
        repo,
        cfg,
        log,
        st,
        it,
        wt,
        item,
        gates,
        forced_by,
        pending_approval,
        agent=agent,
        borrowed=borrowed,
        keep=keep,
        outside=outside,
        dirty=dirty,
        refreshed=refreshed,
        sha=sha,
        branch_head=branch_head,
        landed_note=landed_note,
        merge_since=merge_since,
        callers=(called_from, shell_cwd),
        on_progress=on_progress,
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


def _what_to_land(
    repo: Path, cfg, st, it, target: str, branch: str, called_from: Path | None
) -> tuple[W.Worktree, list[str], list[str] | None] | O.Outcome:
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
    return wt, dirty, _outside_globs(repo, it, target, picked, cfg)


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


def _outside_globs(repo: Path, it, target: str, branch: str, cfg) -> list[str] | None:
    """Paths the landing changes that the item never declared; None when git could not list
    them -- "could not tell", never "none" (B4e42502034).

    A borrowed branch can carry more than this item's work -- another item's commits made
    in the same tree -- and landing that should at least not be silent. Reported, not
    refused: an item's globs are often narrower than its honest diff, and the caller named
    or stood on this branch. ddflow's own bookkeeping paths are not the item's to declare.
    """

    # -z (via git_paths): a non-ASCII name is not C-quoted into one no glob matches;
    # --no-renames: a rename lists its old path too, which the landing removes (B20dc45f4c5).
    changed = CH.changed_paths(repo, target, tip=branch, include=("committed",))
    if changed is None:
        return None
    # A new file matching `[lease] new_file_globs` (a manifest fragment) is its author's own.
    fresh: set[str] = set()
    ng = new_file_globs(cfg)
    if ng and any(path_in_glob(p, g) for p in changed for g in ng):
        added = CH.changed_paths(repo, target, tip=branch, include=("committed",), diff_filter="A")
        if added is None:
            return None
        fresh = {p for p in added if any(path_in_glob(p, g) for g in ng)}
    return [
        p
        for p in changed
        if not p.startswith(SELF_MANAGED)
        and p not in fresh
        and not any(path_in_glob(p, g) for g in it.globs)
    ]


def _open_request_outcome(  # noqa: PLR0913 -- each arg is distinct state the caller already holds
    repo: Path,
    cfg,
    log,
    it,
    wt,
    item: str,
    gates,
    forced_by,
    pending_approval: str,
    agent: str,
    *,
    borrowed: bool,
    message: str,
    model: str,
    dirty: list[str],
) -> O.Outcome:
    """The PR branch, reached only after `_override` ran the require_gates check (Bc6c07dfe52):
    opening a request past unsettled required gates is never silently skipped. A forced
    request spends the approval and records a `merge.forced` event only when the request
    actually opened (the branch head is the proposed landing, `pr sync` lands it)."""
    if borrowed:  # claimed --no-worktree
        return O.refused(
            "worktree.merged",
            f"{item} was claimed without a worktree, and a pull request is opened from "
            f"the item's own branch. Push {wt.branch!r} and open the request yourself, "
            f"or claim {item} with a worktree.",
            id=item,
            sha="",
            dirty=[],
        )
    req = _open_request(repo, cfg, log, it, message=message, model=model, dirty=dirty)
    if req.exit != 0:
        return req  # a refused/unavailable open is not an override to record
    if gates.unmet:
        req.data["awaiting_gates"] = list(gates.awaiting())
    if forced_by is not None:
        if pending_approval:
            forced_by = _spend_after_land(log, cfg, item, gates, forced_by, pending_approval)
        _record_forced(log, item, W.head_sha(wt.path) or "", gates, forced_by, agent)
        req.data["forced"] = True
    return req


def _open_request(
    repo: Path, cfg, log, it, *, message: str, model: str, dirty: list[str]
) -> O.Outcome:

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
