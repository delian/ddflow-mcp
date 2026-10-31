"""PR flow: syncing requests with the forge.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Config
from ...core import flow as F
from ...core.model import REVIEW, Item
from ...infra import forge as FG
from ...infra import git as GIT
from ...infra import proc as P
from ...infra import worktree as W
from ...infra.log import EventLog
from .. import completion as CM
from .. import gates as G
from ..cleanup import dispose_tree
from ..gates import failed_review as GO
from ..gates import measured as GR
from ._core import _state, target
from ._cut import _sync_releases
from ._report import Change, SyncReport


def _changed(it: Item, info: FG.PRInfo) -> bool:
    pr = it.pr
    if pr is None:
        return True
    return (
        pr.review,
        pr.checks,
        pr.base,
        pr.head_sha,
        pr.state,
        pr.queue,
        pr.queue_position,
        pr.queue_state,
    ) != (
        info.review,
        info.checks,
        info.base,
        info.head_sha,
        info.state,
        info.queue,
        info.queue_position,
        info.queue_state,
    )


def _changed_but_queue(it: Item, info: FG.PRInfo) -> bool:
    """`_changed`, ignoring the merge queue: what the old "updated" line was about."""
    pr = it.pr
    if pr is None:
        return True
    return (pr.review, pr.checks, pr.base, pr.head_sha, pr.state) != (
        info.review,
        info.checks,
        info.base,
        info.head_sha,
        info.state,
    )


def _remove_tree(repo: Path, cfg: Config, log: EventLog, it: Item, info: FG.PRInfo) -> str:
    """Remove a merged item's tree, but only when it holds exactly what merged.

    `W.remove` without force refuses any tree whose commits are not on the local base --
    which after a forge-side squash or rebase is EVERY tree, so it would never remove
    one. Forcing it is safe only on proof: no uncommitted file, and a HEAD equal to the
    head the forge merged. Anything else is work the merge did not contain.
    """
    if it.adopted or not cfg.worktree.remove_on_merge or not it.worktree:
        return ""
    path = W.load_path(repo, it.worktree)
    if not path.exists():
        return ""
    wt = W.Worktree(item=it.id, path=path, branch=it.branch, base=info.base)
    if W.dirty(wt):
        return f"kept {path}: uncommitted files the merge did not contain"
    if info.head_sha and W.head_sha(path) != info.head_sha:
        return f"kept {path}: its HEAD is not the head that merged"
    gone = dispose_tree(
        repo,
        cfg,
        log,
        wt,
        item=it,
        forced_by="no uncommitted file, HEAD is the head the forge merged",
    )
    return "" if gone.removed else f"kept {path}: {gone.why}"


def _diff_patch_id(repo: Path, a: str, b: str) -> str:
    """The patch-id of the combined change ``a`` -> ``b``; "" when git cannot say."""
    diff = GIT.run(repo, "diff", "--no-ext-diff", a, b, binary=True)
    if not diff.ok or not diff.out_bytes:
        return ""
    ids = GIT.run(repo, "patch-id", "--stable", input=diff.out_bytes)
    out = ids.out.split() if ids.ok else []
    return out[0] if out else ""


def _rebase_start(repo: Path, info: FG.PRInfo, base_before: str, rebased: bool) -> str:
    """Where a rebase-merge's N commits begin ("" when this is not one, or cannot be told).

    Two proofs, strongest first. The base tip ddflow read BEFORE it merged -- only when
    ddflow asked for a rebase, since a squash's range can match the count by coincidence --
    when the range from it holds exactly the request's commits (a commit someone else
    landed in between breaks the count, which is what the check is for). Otherwise the change itself: the
    request's own diff has the same patch-id as the last N commits on the base and NOT as
    the last one alone -- which also settles a merge a person did, and one a merge queue did.
    """
    sha, n = info.merge_sha, info.commits
    if rebased and base_before and W.rev(repo, base_before):
        counted = W.git(repo, "rev-list", "--count", f"{base_before}..{sha}")
        if W.git(repo, "merge-base", "--is-ancestor", base_before, sha).ok and counted.out == str(
            n
        ):
            return W.rev(repo, base_before)
    fork = W.git(repo, "merge-base", info.head_sha, sha)
    mine = _diff_patch_id(repo, fork.out, info.head_sha) if fork.ok and fork.out else ""
    if not mine or _diff_patch_id(repo, f"{sha}^1", sha) == mine:
        return ""  # unprovable, or a squash (its first parent is already exact)
    start = W.rev(repo, f"{sha}~{n}")
    return start if start and _diff_patch_id(repo, start, sha) == mine else ""


#: `git rev-list --parents` prints the commit and its parents: two words = one parent.
_SINGLE_PARENT = 2


def _landing(repo: Path, cfg: Config, info: FG.PRInfo, base_before: str = "") -> dict[str, str]:
    """``{"landed_before", "landed_after"}``: what a cherry-pick port applies (B178).

    A merge commit or a squash lands ONE commit on the target, so its first parent is the
    old target. A rebase lands N, and the first parent is then the second-to-last of them:
    a port of that range carried only the last commit.
    """
    sha = info.merge_sha
    if not sha or not W.rev(repo, sha):
        return {}
    parents = W.git(repo, "rev-list", "--parents", "-n", "1", sha).out.split()
    before = W.rev(repo, f"{sha}^1")
    if len(parents) <= _SINGLE_PARENT and info.commits > 1:
        rebased = cfg.worktree.merge_strategy == "ff-only"
        before = _rebase_start(repo, info, base_before, rebased) or before
    return {"landed_before": before, "landed_after": sha}


def _settle_merged(
    repo: Path,
    cfg: Config,
    log: EventLog,
    forge: FG.Forge,
    it: Item,
    info: FG.PRInfo,
    rep: SyncReport,
    base_before: str = "",
) -> None:
    data = info.event_data(forge.name)
    data["kind"] = it.kind
    log.append("pr.merged", it.id, data)
    sha = info.merge_sha or info.head_sha
    # What landed, as a range on the target -- a cherry-pick port applies exactly this.
    # The forge merged remotely, so fetch first; a merge or squash commit's first parent
    # is the target just before it. A rebase-merge lands several commits and its first
    # parent is not the old target: `_landing` finds the real start (B178).
    W.fetch(repo, cfg.flow.remote, info.base)
    landed = _landing(repo, cfg, info, base_before)
    log.append("worktree.merged", it.id, {"sha": sha, "branch": it.branch, **landed})
    # A human `merge` gate is the person's to clear: nothing is recorded, and the merge,
    # which is a fact, still settles below.
    GR.record_merge(
        log,
        cfg,
        repo,
        it,
        "passed",
        gates=G.load_gates(repo, cfg),
        evidence={
            "pr": info.url,
            "merge_sha": info.merge_sha,
            "head_sha": info.head_sha,
            "review": info.review,
            "checks": info.checks,
        },
    )
    rep.changes.append(Change(it.id, "merged", f"into {info.base}", info.url))
    line = F.effective_line(_state(log), it)
    for extra in F.back_merge_targets(it, cfg, W.default_branch(repo), line):
        _back_merge(repo, cfg, log, forge, it, extra, rep)
    st = _state(log)
    model = it.pr.author_model if it.pr else ""
    v = CM.verdict(st, cfg, it.id, repo=repo, model=model)
    # D-failed-review-gate: the same refusal `complete` applies; a merge is a fact, this is not.
    v.blockers += GO.failed_review_blockers(st, cfg, it.id, G.load_gates(repo, cfg))
    if v.may_complete:
        log.append(
            "item.completed",
            it.id,
            {"sha": sha, "kind": it.kind, "forced": False, "overridden": [], "pr": info.url},
        )
        rep.changes.append(Change(it.id, "completed", sha[:12], info.url))
    else:
        # Merged is a fact; complete is a judgement. When they disagree a person must
        # see it -- recording DONE would claim gates that did not pass, and leaving it
        # in REVIEW would re-poll a merged request forever.
        reason = f"merged as {info.url} but cannot complete: " + "; ".join(v.blockers)
        log.append("item.blocked", it.id, {"reason": reason})
        rep.changes.append(Change(it.id, "blocked", reason, info.url))
    kept = _remove_tree(repo, cfg, log, it, info)
    if kept:
        rep.changes.append(Change(it.id, "kept_tree", kept))
    # Anything stacked on this branch now belongs on the real target.
    for other in st.items.values():
        if other.state == REVIEW and other.pr and other.pr.base == it.branch:
            new_base = target(repo, cfg, other, st)
            try:
                forge.set_base(other.pr.number, new_base)
            except (FG.ForgeError, FG.ForgeUnavailable) as exc:
                rep.refused.append(f"{other.id}: could not retarget to {new_base}: {exc}")
                continue
            log.append(
                "pr.synced",
                other.id,
                {"base": new_base, "number": other.pr.number, "kind": other.kind},
            )
            rep.changes.append(
                Change(other.id, "retargeted", f"{it.branch} -> {new_base}", other.pr.url)
            )


def _back_merge(
    repo: Path, cfg: Config, log: EventLog, forge: FG.Forge, it: Item, into: str, rep: SyncReport
) -> None:
    """A gitflow hotfix must reach develop too, or the next release re-breaks production.

    Opened as a request of its own, because develop is protected in exactly the
    repositories that route work through requests. The item's work is done once production
    has it, so the request is tracked separately (`State.back_merges`) and every later
    `pr sync` re-asks about it until it lands or is closed (B171).

    FROM production, not from the hotfix branch: a forge that deletes merged head
    branches has already deleted it by now, and production is where the fix is.
    """
    source = F.production(cfg, W.default_branch(repo))
    try:
        found = forge.find(source)
        if found is not None and found.state == "open" and found.base == into:
            _track_back_merge(log, forge, it, into, found)
            rep.changes.append(Change(it.id, "back_merge", f"already open into {into}", found.url))
            return
        info = forge.create(
            head=source,
            base=into,
            title=f"{it.id}: back-merge into {into}",
            body=f"Back-merge of hotfix {it.id} into {into}.\n\nItem: {it.id}",
            draft=False,
            labels=list(cfg.flow.pr_labels),
            reviewers=list(cfg.flow.pr_reviewers),
        )
        _track_back_merge(log, forge, it, into, info)
        rep.changes.append(Change(it.id, "back_merge", f"opened into {into}", info.url))
    except (FG.ForgeError, FG.ForgeUnavailable) as exc:
        rep.refused.append(f"{it.id}: back-merge into {into} could not be opened: {exc}")


def _track_back_merge(log: EventLog, forge: FG.Forge, it: Item, into: str, info: FG.PRInfo) -> None:
    log.append(
        "backmerge.recorded",
        it.id,
        {
            "into": into,
            "number": info.number,
            "url": info.url,
            "forge": forge.name,
            "state": info.state,
        },
    )


def _sync_back_merges(log: EventLog, forge: FG.Forge, rep: SyncReport, *, only: str = "") -> None:
    """Re-ask about every back-merge request still open: it landed, or it was closed
    without merging -- in which case develop does NOT have a fix production already ships,
    and the next release would re-break it (B171)."""
    for rec in [r for r in _state(log).back_merges.values() if r["state"] == "open"]:
        if only and rec["item"] != only:
            continue
        try:
            info = forge.view(int(rec["number"]))
        except FG.ForgeUnavailable as exc:
            rep.unavailable.append(f"{rec['item']}: back-merge #{rec['number']}: {exc}")
            return
        except FG.ForgeError as exc:
            rep.refused.append(f"{rec['item']}: back-merge #{rec['number']}: {exc}")
            continue
        if info.state == "open":
            rep.waiting.append(
                {
                    "id": rec["item"],
                    "url": info.url,
                    "back_merge": rec["into"],
                    "review": "",
                    "checks": "",
                    "base": rec["into"],
                }
            )
            continue
        log.append(
            "backmerge.recorded",
            rec["item"],
            {**{k: rec[k] for k in ("into", "number", "url", "forge")}, "state": info.state},
        )
        if info.state == "merged":
            rep.changes.append(
                Change(rec["item"], "back_merge", f"merged into {rec['into']}", info.url)
            )
        else:
            rep.refused.append(
                f"{rec['item']}: back-merge request into {rec['into']} was closed without "
                f"merging ({info.url}) -- {rec['into']} does not have the hotfix. Merge production "
                f"into {rec['into']} yourself; ddflow stops re-checking a closed request."
            )


def sync(repo: Path, cfg: Config, log: EventLog, *, only: str = "") -> SyncReport:
    """Ask the forge about every request in REVIEW, and record what changed."""
    rep = SyncReport()
    bad = F.problems(cfg)
    if bad:
        rep.refused.extend(bad)
        return rep
    st = _state(log)
    waiting = [
        it
        for it in sorted(st.items.values(), key=lambda i: i.id)
        if it.state == REVIEW and it.pr and it.pr.number and (not only or it.id == only)
    ]
    open_back = [
        r
        for r in st.back_merges.values()
        if r["state"] == "open" and (not only or r["item"] == only)
    ]
    if not waiting and not st.pending_releases and not open_back:
        return rep
    try:
        forge = FG.detect(repo, cfg)
    except FG.ForgeUnavailable as exc:
        rep.unavailable.append(str(exc))
        return rep
    for it in waiting:
        rep.checked += 1
        try:
            info = forge.view(it.pr.number)
        except FG.ForgeUnavailable as exc:
            # Stop at the first: an unreachable forge is unreachable for all of them, and
            # `next` runs this -- N timeouts in a row would stall every agent's turn.
            rest = [w.id for w in waiting[waiting.index(it) + 1 :]]
            rep.unavailable.append(
                f"{it.id}: {exc}" + (f" (not asked: {', '.join(rest)})" if rest else "")
            )
            return rep
        except FG.ForgeError as exc:
            rep.refused.append(f"{it.id}: {exc}")
            continue
        _apply(repo, cfg, log, forge, it, info, rep)
    _sync_back_merges(log, forge, rep, only=only)
    if not only:
        _sync_releases(repo, cfg, log, forge, rep)
    return rep


def _stale_review(it: Item, info: FG.PRInfo) -> bool:
    """Was the deciding review made on an OLDER head than the one there now?

    Forges keep reporting a decision until someone reviews again. So without this, a
    change request answered by a push sent the item back to the queue on every sync, and
    an approval of yesterday's head merged whatever was pushed after it -- wherever the
    forge is not set to dismiss stale approvals, which is its default on GitHub.
    """
    if info.review not in ("approved", "changes_requested") or not info.head_sha:
        return False
    if info.review_sha:
        return info.review_sha != info.head_sha
    # The forge did not say which commit was reviewed (GitLab). A change request already
    # recorded against an earlier head has been answered by the push since.
    pr = it.pr
    return bool(
        info.review == "changes_requested"
        and pr is not None
        and pr.requested_head
        and pr.requested_head != info.head_sha
    )


def _landed(
    repo: Path, cfg: Config, it: Item, info: FG.PRInfo, log: EventLog, rep: SyncReport
) -> bool:
    """A request merged into something OTHER than the target -- a stacked one merged by a
    person into its dependency's branch -- has landed only once that commit is on the target.

    Settling it earlier recorded DONE for work the target does not have, unblocked its
    dependents against a base missing its code, and removed its tree.
    """
    final = target(repo, cfg, it, _state(log))
    sha = info.merge_sha or info.head_sha
    remote = cfg.flow.remote
    W.fetch(repo, remote, final)
    ref = f"{remote}/{final}" if W.rev(repo, f"{remote}/{final}") else final
    if sha and W.rev(repo, sha) and W.git(repo, "merge-base", "--is-ancestor", sha, ref).ok:
        return True
    owner = next(
        (o for o in _state(log).items.values() if o.branch == info.base and o.id != it.id), None
    )
    if owner is not None and owner.state == REVIEW:
        rep.waiting.append(
            {
                "id": it.id,
                "url": info.url,
                "review": info.review,
                "checks": info.checks,
                "base": info.base,
                "note": f"merged into {info.base}; lands on {final} with {owner.id}",
            }
        )
        return False
    reason = (
        f"merged into {info.base} ({info.url}), which is not {final} and is not on its way "
        f"there ({owner.id + ' is ' + owner.state if owner else 'no open item owns it'}). "
        f"The work is not on {final}: re-open it against {final}, or abandon it."
    )
    log.append("item.blocked", it.id, {"reason": reason})
    rep.changes.append(Change(it.id, "blocked", reason, info.url))
    return False


def _apply(
    repo: Path,
    cfg: Config,
    log: EventLog,
    forge: FG.Forge,
    it: Item,
    info: FG.PRInfo,
    rep: SyncReport,
) -> None:
    if _stale_review(it, info):
        info.review = "pending"
    if not info.queue_known and it.pr is not None and info.state == "open":
        # The queue could not be asked this time: keep what was last seen rather than
        # reading silence as "ejected" (and then merging a request that is still queued).
        info.queue = it.pr.queue
        info.queue_position, info.queue_state = it.pr.queue_position, it.pr.queue_state
    data = info.event_data(forge.name)
    data["kind"] = it.kind
    if info.state == "merged":
        if info.base != target(repo, cfg, it, _state(log)) and not _landed(
            repo, cfg, it, info, log, rep
        ):
            return
        _settle_merged(repo, cfg, log, forge, it, info, rep)
        return
    if info.state == "closed":
        log.append("pr.closed", it.id, data)
        rep.changes.append(Change(it.id, "closed", "closed without merging", info.url))
        return
    if info.review == "changes_requested":
        data["block"] = cfg.flow.on_changes_requested == "block"
        log.append("pr.changes_requested", it.id, data)
        rep.changes.append(Change(it.id, "changes_requested", info.feedback[:200], info.url))
        return
    was_queued = bool(it.pr and it.pr.queue)
    moved = _changed(it, info)
    if moved:
        log.append("pr.synced", it.id, data)
        if _changed_but_queue(it, info):
            rep.changes.append(
                Change(
                    it.id,
                    "updated",
                    f"review={info.review or '-'} checks={info.checks or '-'}",
                    info.url,
                )
            )
    _report_queue(it, info, moved, was_queued, rep)
    final = target(repo, cfg, it, _state(log))
    ready = (
        cfg.flow.pr_merge == "on_approval"
        and info.review == "approved"
        and info.checks in ("passing", "")
        and not info.draft
        # Already in the merge queue: asking the forge to merge it again is not a retry,
        # it is noise (and on some forges a second enqueue).
        and not info.queue
        # ...and not while the queue could not be asked: "unknown" is "maybe queued", and a
        # second `gh pr merge` on a queued request is a second enqueue.
        and info.queue_known
        # A stacked request merges into its dependency's branch, not into the target;
        # merging it there first would land it on the target UNREVIEWED as part of the
        # dependency's merge. It waits to be retargeted.
        and info.base == final
    )
    if not ready:
        rep.waiting.append(_waiting(it.id, info))
        return
    try:
        # The target's tip BEFORE the forge merges: where a rebase's commits will start.
        base_before = _remote_tip(repo, cfg, info.base)
        forge.merge(
            info.number, strategy=cfg.worktree.merge_strategy, head_sha=info.head_sha, auto=False
        )
        after = forge.view(info.number)
    except FG.ForgeUnavailable as exc:
        rep.unavailable.append(f"{it.id}: {exc}")
        return
    except FG.ForgeError as exc:
        # Branch protection, a head that moved: the forge's rules win.
        rep.refused.append(f"{it.id}: approved, but the forge refused the merge: {exc}")
        return
    if after.state == "merged":
        rep.changes.append(Change(it.id, "merged_by_ddflow", "approved and green", after.url))
        _settle_merged(repo, cfg, log, forge, _state(log).items[it.id], after, rep, base_before)
        return
    if after.queue:
        # `gh pr merge` on a queue-protected branch ENQUEUES: the request stays open while
        # the queue builds it. Said as what it is, not as "waiting".
        qdata = after.event_data(forge.name)
        qdata["kind"] = it.kind
        log.append("pr.synced", it.id, qdata)
        rep.changes.append(Change(it.id, "queued", _queue_detail(after), after.url))
    rep.waiting.append({**_waiting(it.id, after), "queued": True})


def _remote_tip(repo: Path, cfg: Config, branch: str) -> str:
    """The remote's current tip of ``branch`` ("" when it cannot be read).

    Asked of the remote itself (`ls-remote`): it needs no remote-tracking ref (a
    single-branch clone or an odd refspec has none) and no shared FETCH_HEAD (parallel
    worktrees fetch concurrently). The fetch is only to have the objects. This is a snapshot
    taken BEFORE the forge merges, which is all `_rebase_start` asks of it: it checks the
    range by count and by the change itself, so a tip that moved (or an unreadable one)
    makes that proof decline (the change-itself proof may still hold); only when both
    decline does the old first parent stand, as before B178.
    """
    remote = cfg.flow.remote
    try:
        asked = W.git(repo, "ls-remote", remote, f"refs/heads/{branch}", timeout=60)
    except P.TimeoutExpired:
        return ""
    tip = asked.out.split()[0] if asked.ok and asked.out else ""
    if not tip or not W.fetch(repo, remote, branch).ok:
        return ""
    return tip if W.rev(repo, tip) else ""


def _report_queue(
    it: Item, info: FG.PRInfo, moved: bool, was_queued: bool, rep: SyncReport
) -> None:
    """Say what the merge queue did since the last look: entered, moved, or ejected."""
    if info.queue:
        same_place = (
            was_queued and it.pr is not None and it.pr.queue_position == info.queue_position
        )
        if moved and not same_place:
            rep.changes.append(Change(it.id, "queued", _queue_detail(info), info.url))
    elif was_queued and info.state == "open" and info.queue_known:
        rep.changes.append(
            Change(
                it.id,
                "queue_ejected",
                "ejected from the merge queue without merging (the queue's own checks failed, "
                f"or someone dequeued it); checks={info.checks or '-'}. Fix it and it is "
                "queued again once approved and green.",
                info.url,
            )
        )


def _queue_detail(info: FG.PRInfo) -> str:
    where = f"position {info.queue_position}" if info.queue_position else "position unknown"
    return f"in the merge queue, {where}" + (f" ({info.queue_state})" if info.queue_state else "")


def _waiting(item: str, info: FG.PRInfo) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": item,
        "url": info.url,
        "review": info.review,
        "checks": info.checks,
        "base": info.base,
    }
    if info.queue:
        row.update(queued=True, queue_position=info.queue_position, queue_state=info.queue_state)
    if not info.queue_known:
        row["note"] = (
            "the merge queue could not be read, so ddflow will not merge this until it can "
            "(a second `gh pr merge` on a queued request is a second enqueue)"
        )
    return row
