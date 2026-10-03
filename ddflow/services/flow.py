"""Pull requests and version tags: landing work where merges need approval (RESEARCH R16).

The agent's loop in a PR-governed repository is the same loop as anywhere else — claim,
implement, pass the gates, `merge`, take the next task. What changes is what `merge`
MEANS: it pushes the branch and opens a request, releases the lease, and parks the item
in REVIEW. Nobody waits. `sync` is the other half, and it is what turns a reviewer's
action back into queue state:

    merged              -> the merge gate passes on the forge's evidence, the item
                           completes (or is parked with the reason it cannot), dependents
                           that stacked on it are retargeted, its tree is removed
    changes requested   -> back in the queue WITH the review text, for any agent to fix
    closed              -> parked for a person: a "no" is not something to retry
    approved + green    -> merged by ddflow, when [flow].pr_merge = "on_approval"

Nothing here prints; `api/flow.py` turns the reports into Outcomes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import flow as F
from ..core.model import DONE, REVIEW, GateRecord, Item, State, fold
from ..infra import forge as FG
from ..infra import proc as P
from ..infra import worktree as W
from ..infra.log import EventLog
from . import completion as CM
from . import gates as G
from . import leases as L


def _state(log: EventLog) -> State:
    return fold(log.read_all(), strict=False)


def target(repo: Path, cfg: Config, it: Item, st: State) -> str:
    """Where ``it`` lands: its release line's branch, or what `model` says for the current one."""
    return F.target_branch(it, cfg, W.default_branch(repo), F.effective_line(st, it))


def stacked_on(st: State, it: Item) -> Item | None:
    """The dependency whose unmerged branch ``it`` forked from, if it is still unmerged."""
    if not it.base:
        return None
    for other in st.items.values():
        if other.id != it.id and other.branch == it.base and other.state == REVIEW:
            return other
    return None


def fork_point(repo: Path, cfg: Config, st: State, it: Item) -> tuple[str, str]:
    """(base, branch) for a new worktree: stacked on a dependency, or off the target."""
    from ..core.schedule import inherited_deps

    branch = F.branch_name(it, cfg)
    stack = F.stack_base(st, it, cfg, [d for _, d in inherited_deps(st, it)])
    if stack.base:
        return stack.base, branch
    base = target(repo, cfg, it, st)
    if cfg.flow.integration == "pr":
        # Fork from what the forge HAS, not from a local branch nobody pulls: in PR mode
        # every merge happens remotely, so the local base is stale by construction.
        remote = cfg.flow.remote
        if W.fetch(repo, remote, base).ok and W.rev(repo, f"{remote}/{base}"):
            return f"{remote}/{base}", branch
    return base, branch


# -- opening ------------------------------------------------------------------------


@dataclass
class Opened:
    item: str
    ok: bool = False
    refused: bool = False
    unavailable: bool = False
    reason: str = ""
    number: int = 0
    url: str = ""
    base: str = ""
    head: str = ""
    stacked_on: str = ""
    created: bool = False
    warnings: list[str] = field(default_factory=list)


def pre_merge_blockers(
    st: State, cfg: Config, item_id: str, *, repo: Path, model: str
) -> list[str]:
    """What would stop this item completing ONCE MERGED. Asked before a request opens.

    A request is where a person's time goes. Opening one for work whose own pipeline is
    unfinished spends that time on something ddflow would then refuse to complete -- and
    the refusal arrives after the merge, when it can no longer stop anything. So the
    verdict is computed as if the merge gate had passed, and anything left is a reason
    not to ask anyone yet.
    """
    hypo = copy.deepcopy(st)
    it = hypo.items[item_id]
    it.gates["merge"] = GateRecord(gate="merge", outcome="passed")
    return CM.verdict(hypo, cfg, item_id, repo=repo, model=model).blockers


def pr_body(st: State, it: Item, *, on: Item | None) -> str:
    lines = [it.body.strip() or it.title, ""]
    if on is not None and on.pr:
        lines += [
            f"**Stacked on {on.id}** ({on.pr.url}). Review that first; this request "
            f"is retargeted automatically once it merges.",
            "",
        ]
    ran = [(g, r.outcome) for g, r in it.gates.items() if r.outcome]
    if ran:
        lines += ["| gate | outcome |", "|---|---|"]
        lines += [f"| {g} | {o} |" for g, o in ran]
        lines.append("")
    lines.append(f"Item: {it.id}")
    return "\n".join(lines)


def open_request(
    repo: Path,
    cfg: Config,
    log: EventLog,
    item_id: str,
    *,
    title: str = "",
    model: str = "",
) -> Opened:
    st = _state(log)
    it = st.items[item_id]
    out = Opened(item=item_id, head=it.branch)
    bad = F.problems(cfg)
    if bad:
        out.refused, out.reason = True, "; ".join(bad)
        return out
    blockers = pre_merge_blockers(st, cfg, item_id, repo=repo, model=model)
    if blockers:
        out.refused = True
        out.reason = (
            f"not opening a request for {item_id}: it could not complete even once "
            f"merged, so a reviewer's time would be spent on something ddflow would then "
            f"refuse:\n" + "\n".join(f"  - {b}" for b in blockers)
        )
        return out
    try:
        forge = FG.detect(repo, cfg)
    except FG.ForgeUnavailable as exc:
        out.unavailable, out.reason = True, str(exc)
        return out
    on = stacked_on(st, it)
    out.base = on.branch if on is not None else target(repo, cfg, it, st)
    out.stacked_on = on.id if on is not None else ""
    path = W.load_path(repo, it.worktree)
    pushed = W.push(path, cfg.flow.remote, it.branch)
    if not pushed.ok:
        out.refused = True
        out.reason = (
            f"push of {it.branch} to {cfg.flow.remote} was rejected: {pushed.err or pushed.out}\n"
            f"If someone committed to the branch on the forge, `git -C {path} pull` and "
            f"merge again. ddflow never force-pushes."
        )
        return out
    try:
        info = forge.find(it.branch)
        if info is not None and info.state == "open":
            if info.base != out.base:
                forge.set_base(info.number, out.base)
                info = forge.view(info.number)
        else:
            info = forge.create(
                head=it.branch,
                base=out.base,
                title=title or f"{it.id}: {it.title}",
                body=pr_body(st, it, on=on),
                draft=cfg.flow.pr_draft,
                labels=list(cfg.flow.pr_labels),
                reviewers=list(cfg.flow.pr_reviewers),
            )
            out.created = True
    except FG.ForgeUnavailable as exc:
        out.unavailable, out.reason = True, str(exc)
        return out
    except FG.ForgeError as exc:
        out.refused, out.reason = True, str(exc)
        return out
    data = info.event_data(forge.name)
    data.update(kind=it.kind, author_model=model, review="pending")
    log.append("pr.opened", item_id, data)
    L.release(log, item_id, note=f"in review: {info.url}")
    # The first request is where review policy starts to matter: record whatever was not
    # chosen, so it is followed from here rather than re-decided by the next ddflow.
    from . import choices as CH

    CH.adopt_defaults(log, cfg, ["pr_merge", "on_changes_requested", "stack"])
    out.ok, out.number, out.url = True, info.number, info.url
    if cfg.flow.pr_merge == "auto" and on is None:
        try:
            forge.merge(
                info.number,
                strategy=cfg.worktree.merge_strategy,
                head_sha=info.head_sha,
                auto=True,
            )
        except (FG.ForgeError, FG.ForgeUnavailable) as exc:
            out.warnings.append(
                f"auto-merge could not be requested ({exc}); `pr sync` will still "
                f"complete the item once a person merges it."
            )
    return out


# -- syncing ------------------------------------------------------------------------


@dataclass
class Change:
    item: str
    what: str  # merged | completed | blocked | changes_requested | closed | merged_by_ddflow | retargeted | updated | back_merge | queued | queue_ejected
    detail: str = ""
    url: str = ""


@dataclass
class SyncReport:
    checked: int = 0
    changes: list[Change] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


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
    r = W.remove(repo, cfg, wt, force=True)
    if r.ok:
        log.append("worktree.removed", it.id, {"path": it.worktree})
        return ""
    return f"kept {path}: {r.err or r.out}"


def _diff_patch_id(repo: Path, a: str, b: str) -> str:
    """The patch-id of the combined change ``a`` -> ``b``; "" when git cannot say."""
    diff = P.run(["git", "-C", str(repo), "diff", "--no-ext-diff", a, b], capture_output=True)
    if diff.returncode != 0 or not diff.stdout:
        return ""
    ids = P.run(
        ["git", "-C", str(repo), "patch-id", "--stable"], input=diff.stdout, capture_output=True
    )
    out = ids.stdout.decode("utf-8", "replace").split() if ids.returncode == 0 else []
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
    G.record(
        log,
        cfg,
        it.id,
        "merge",
        "passed",
        evidence={
            "pr": info.url,
            "merge_sha": info.merge_sha,
            "head_sha": info.head_sha,
            "review": info.review,
            "checks": info.checks,
        },
        gates=G.load_gates(repo, cfg),
    )
    rep.changes.append(Change(it.id, "merged", f"into {info.base}", info.url))
    line = F.effective_line(_state(log), it)
    for extra in F.back_merge_targets(it, cfg, W.default_branch(repo), line):
        _back_merge(repo, cfg, log, forge, it, extra, rep)
    st = _state(log)
    model = it.pr.author_model if it.pr else ""
    v = CM.verdict(st, cfg, it.id, repo=repo, model=model)
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
    worktrees fetch concurrently). The fetch is only to have the objects. A tip that moved
    between the two, or whose objects are missing, is safe: `_rebase_start` proves the range
    by count and by the change itself, and falls back to the old first parent when neither
    holds.
    """
    remote = cfg.flow.remote
    asked = W.git(repo, "ls-remote", remote, f"refs/heads/{branch}")
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
    elif was_queued and info.state == "open":
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


# -- versions -----------------------------------------------------------------------


@dataclass
class VersionPlan:
    ref: str
    current: str = ""
    current_tag: str = ""
    next: str = ""
    bump: str = ""
    reasons: list[str] = field(default_factory=list)
    commits: int = 0
    items: list[str] = field(default_factory=list)
    notes: str = ""
    problems: list[str] = field(default_factory=list)
    line: str = ""


def release_source(repo: Path, cfg: Config, line: str = "") -> str:
    """The branch a release is cut FROM: a maintenance line's branch, develop under
    gitflow, else the base branch."""
    if F.is_maintenance(cfg, line):
        return cfg.flow.lines[line]
    if cfg.flow.model == F.GITFLOW:
        return cfg.flow.develop_branch
    return cfg.worktree.base_ref or W.default_branch(repo)


def _ref(repo: Path, cfg: Config, branch: str) -> str:
    """Local branch, or the remote's copy of it in PR mode -- where merges happen."""
    if cfg.flow.integration == "pr":
        remote = cfg.flow.remote
        if W.fetch(repo, remote, branch).ok and W.rev(repo, f"{remote}/{branch}"):
            return f"{remote}/{branch}"
    return branch


def _set_next(vp: VersionPlan, cfg: Config, version: str, line: str) -> None:
    """The next version: given, else bumped from the current one, else the initial one."""
    if version:
        if F.parse_version(version) is None:
            vp.problems.append(f"{version!r} is not MAJOR.MINOR.PATCH")
            vp.next = version
            return
        if vp.current and F.parse_version(version) <= F.parse_version(vp.current):
            vp.problems.append(f"{version} is not above the current {vp.current}")
        vp.next = version
    elif not vp.commits:
        vp.next = ""
    elif not vp.current:
        vp.next = cfg.flow.initial_version
    else:
        vp.next = F.fmt(F.bump(F.parse_version(vp.current), vp.bump))
    if (
        F.is_maintenance(cfg, line)
        and vp.current
        and vp.next
        and F.parse_version(vp.next)[0] != F.parse_version(vp.current)[0]
    ):
        # A maintenance line exists to KEEP its major. A breaking change released from it
        # would claim the next major -- which a newer line already owns.
        vp.problems.append(
            f"{vp.next} leaves the {vp.current.split('.')[0]}.x major, and line {line!r} is a "
            f"maintenance line: a breaking change belongs on the current line. Release "
            f"with --bump minor or --bump patch, or --version."
        )


def reached(repo: Path, cfg: Config, it: Item, ref: str) -> str:
    """The commit of an item's landing that ``ref`` contains, or "" if none.

    ``it.merged_sha`` is the landing on the item's merge target: its merge commit. That
    commit is on the target only. Other lines receive the work as the BRANCH it merged
    -- the merge commit's second parent -- by gitflow's back-merge or a forward-merge
    port, so a hotfix landed on main reaches develop with main's merge commit nowhere in
    it (B9a337697c0).

    The second parent is the merged branch only when ``merged_sha`` IS that landing
    merge commit: the recorded ``landed_after``, whose first parent is the recorded
    ``landed_before``. Before B9f8019c521 ``merged_sha`` was the branch head itself,
    which the first candidate covers -- and whose own second parent, when the branch
    had merged its base in, is just an old base tip that every line already contains.

    A squash landing has one parent and its back-merge is another squash, so neither
    commit reaches the other line by ancestry: such an item is found there by its change
    instead (`_squash_on`: the same patch-id, or ddflow's own back-merge commit).
    A fast-forward landing (``merge_strategy = "ff-only"``) IS the branch head, whose
    second parent, if it has one, is something the branch merged in, not the branch:
    only a ``no-ff`` landing (or a forge's merge commit) has the branch as ``^2``.
    """
    merged = it.merged_sha
    if not merged:
        return ""
    candidates = [merged]
    if (
        (cfg.worktree.merge_strategy == "no-ff" or cfg.flow.integration == "pr")
        and merged == it.landed_after
        and it.landed_before
        and W.rev(repo, f"{merged}^1") == it.landed_before
    ):
        candidates.append(W.rev(repo, f"{merged}^2"))
    for c in candidates:
        if c and W.git(repo, "merge-base", "--is-ancestor", c, ref).ok:
            return c
    return _squash_on(repo, it, merged, ref)


ONE_PARENT = 1

#: How much of a line's history is searched for a squash's copy (patch-id match).
_SQUASH_SEARCH = 1000


def _patch_ids(repo: Path, *log_args: str) -> dict[str, str]:
    """``{patch-id: commit}`` for the commits ``git log`` names, newest first; {} on failure."""
    log = P.run(
        ["git", "-C", str(repo), "log", "-p", "--no-ext-diff", "--no-merges", *log_args],
        capture_output=True,
    )
    if log.returncode != 0 or not log.stdout:
        return {}
    ids = P.run(
        ["git", "-C", str(repo), "patch-id", "--stable"],
        input=log.stdout,
        capture_output=True,
    )
    out: dict[str, str] = {}
    for line in ids.stdout.decode("utf-8", "replace").splitlines() if ids.returncode == 0 else []:
        pid, _, commit = line.partition(" ")
        out.setdefault(pid, commit.strip())
    return out


@lru_cache(maxsize=64)
def _line_patch_ids(repo: str, tip: str) -> dict[str, str]:
    return _patch_ids(Path(repo), f"--max-count={_SQUASH_SEARCH}", tip)


@lru_cache(maxsize=64)
def _line_subjects(repo: str, tip: str) -> tuple[tuple[str, str], ...]:
    log = W.git(
        Path(repo), "log", f"--max-count={_SQUASH_SEARCH}", "--no-merges", "--format=%H %s", tip
    )
    rows = (line.partition(" ") for line in log.out.splitlines()) if log.ok else ()
    return tuple((sha, subj) for sha, _, subj in rows)


def _squash_on(repo: Path, it: Item, merged: str, ref: str) -> str:
    """The commit on ``ref`` carrying a SQUASH landing's change, or "" (B20d45f540c).

    A squash is one parent commit, so no merge parent leads to its branch, and gitflow's
    back-merge of a hotfix is a second squash: neither commit is an ancestor of the other
    line. The change itself is the proof: a commit on ``ref`` with the same patch-id (or,
    where develop has drifted around the hunks and the patch-ids differ, the back-merge
    commit ddflow wrote for this item). Only a single-parent landing is looked up -- a
    merge commit is found by ancestry above.
    """
    parents = W.git(repo, "rev-list", "--parents", "-n", "1", merged).out.split()
    if len(parents) != ONE_PARENT + 1:  # merged + its one parent; "" (unreadable) lands here too
        return ""
    tip = W.rev(repo, ref)
    if not tip:
        return ""
    mine = _patch_ids(repo, "-n", "1", merged)
    if mine:
        if hit := _line_patch_ids(str(repo), tip).get(next(iter(mine))):
            return hit
    subject = f"back-merge {it.id} into "
    for sha, subj in _line_subjects(str(repo), tip):
        if subj.startswith(subject):
            return sha
    return ""


def plan_version(
    repo: Path, cfg: Config, st: State, *, bump: str = "", version: str = "", line: str = ""
) -> VersionPlan:
    branch = release_source(repo, cfg, line)
    ref = _ref(repo, cfg, branch)
    vp = VersionPlan(ref=ref)
    vp.problems = F.problems(cfg)
    if line and line not in F.line_order(cfg):
        vp.problems.append(f"unknown release line {line!r}; lines: {', '.join(F.line_order(cfg))}")
        return vp
    if not W.rev(repo, ref):
        vp.problems.append(f"release source {ref!r} does not exist")
        return vp
    vp.current_tag, vp.current = W.reachable_tag(repo, cfg.flow.tag_prefix, ref)
    messages = W.log_messages(repo, vp.current_tag, ref)
    vp.commits = len(messages)
    bumps: list[str] = []
    for m in messages:
        b = F.commit_bump(m)
        if b:
            bumps.append(b)
            vp.reasons.append(f"{b}: {m.splitlines()[0][:80]}")
    # Released ON THIS LINE. A fix forward-merged from 1.x to 3.x ships in both a 1.x and
    # a 3.x release; excluding it everywhere once either was cut dropped it from the other
    # line's notes and bump (RESEARCH R17 review).
    here = line or cfg.flow.current_line
    released = {
        i for r in st.releases if (r.line or cfg.flow.current_line) == here for i in r.items
    }
    shipped: list[Item] = []
    for it in sorted(st.items.values(), key=lambda i: i.id):
        if it.removed or it.state != DONE or it.kind != "task" or it.id in released:
            continue
        sha = reached(repo, cfg, it, ref)
        if not sha:
            continue
        if vp.current_tag and W.git(repo, "merge-base", "--is-ancestor", sha, vp.current_tag).ok:
            continue
        shipped.append(it)
        b = F.item_bump(it, cfg)
        if b:
            bumps.append(b)
            vp.reasons.append(f"{b}: item {it.id} ({', '.join(it.tags)})")
    vp.items = [i.id for i in shipped]
    vp.bump = bump or F.strongest(bumps) or (F.PATCH if vp.commits else "")
    _set_next(vp, cfg, version, line)
    vp.line = line or cfg.flow.current_line
    vp.notes = release_notes(vp, shipped, messages, cfg)
    return vp


def release_notes(vp: VersionPlan, shipped: list[Item], messages: list[str], cfg: Config) -> str:
    groups: dict[str, list[str]] = {F.MAJOR: [], F.MINOR: [], F.PATCH: [], "": []}
    for it in shipped:
        link = f" ({it.pr.url})" if it.pr and it.pr.url else ""
        groups[F.item_bump(it, cfg)].append(f"- {it.title or it.id} [{it.id}]{link}")
    for m in messages:
        first = m.splitlines()[0]
        if "Item:" in m or first.startswith("Merge "):
            continue  # covered by the item line, or a merge commit's boilerplate
        b = F.commit_bump(m)
        if b:
            groups[b].append(f"- {first}")
    heads = {F.MAJOR: "Breaking changes", F.MINOR: "Features", F.PATCH: "Fixes", "": "Other"}
    out = [f"## {cfg.flow.tag_prefix}{vp.next or '?'}", ""]
    for key in (F.MAJOR, F.MINOR, F.PATCH, ""):
        if groups[key]:
            out += [f"### {heads[key]}", *groups[key], ""]
    return "\n".join(out).rstrip() + "\n"


@dataclass
class Cut:
    version: str = ""
    tag: str = ""
    sha: str = ""
    ok: bool = False
    refused: bool = False
    unavailable: bool = False
    nothing: bool = False
    reason: str = ""
    pushed: bool = False
    url: str = ""
    steps: list[str] = field(default_factory=list)
    plan: VersionPlan | None = None
    changelog: str = ""  # the changelog file the cut wrote (--changelog), "" when none


def cut(
    repo: Path,
    cfg: Config,
    log: EventLog,
    *,
    bump: str = "",
    version: str = "",
    push: bool = False,
    dry_run: bool = False,
    line: str = "",
    changelog: bool = False,
    force: bool = False,
) -> Cut:
    """Tag the next version. Under gitflow, via a release branch.

    trunk              tag the base branch head.
    gitflow + merge    release/X from develop -> merged into production -> tagged there
                       -> the tag merged back into develop, so develop's next version
                       is computed from a tag it actually contains.
    gitflow + pr       release/X is pushed and a request opened into production; the
                       tag is cut by `pr sync` when a person merges it.

    ``changelog`` also writes the version's section into CHANGELOG.md (``changelog_cut``),
    committed on the branch the tag names -- or, in pr mode, on the release branch the
    request carries. Without it nothing but the tag is written.
    """
    st = _state(log)
    vp = plan_version(repo, cfg, st, bump=bump, version=version, line=line)
    out = Cut(plan=vp, version=vp.next)
    if bump and bump not in F.BUMPS:
        vp.problems.append(f"bump {bump!r} is not one of {', '.join(F.BUMPS)}")
    if vp.problems:
        out.refused, out.reason = True, "; ".join(vp.problems)
        return out
    if not vp.next:
        out.nothing = True
        out.reason = (
            f"nothing to release: no commits on {vp.ref} since {vp.current_tag or 'the start'}"
        )
        return out
    out.tag = f"{cfg.flow.tag_prefix}{vp.next}"
    if W.rev(repo, f"refs/tags/{out.tag}"):
        out.refused, out.reason = True, f"tag {out.tag} already exists"
        return out
    if vp.next in st.pending_releases:
        pending = st.pending_releases[vp.next]
        out.refused, out.reason = (
            True,
            f"release {vp.next} is already awaiting review: {pending.get('url')}",
        )
        return out
    direct = cfg.flow.model != F.GITFLOW or F.is_maintenance(cfg, line)
    prep = None
    if changelog:
        if direct and cfg.flow.integration == "pr":
            out.refused, out.reason = (
                True,
                (
                    "--changelog with pull requests needs [flow].model = gitflow: a trunk or "
                    "maintenance cut tags the remote branch as it is, and the changelog commit "
                    "would have no request to travel in"
                ),
            )
            return out
        prep = _prepare_changelog(repo, cfg, out, vp, force=force, dry_run=dry_run)
        if prep is None:
            return out
    if dry_run:
        out.ok = True
        out.steps.append("dry run: nothing written")
        return out
    if direct:
        # A maintenance line is tagged where it stands: it has no develop/production
        # pair of its own, so there is no release branch to route through.
        if prep and not _write_changelog(repo, cfg, out, vp.ref, prep, vp.next, force=force):
            return out
        out.sha = W.rev(repo, vp.ref)
        return _tag_and_push(repo, cfg, log, out, vp, branch=vp.ref, push=push)
    return _cut_gitflow(repo, cfg, log, out, vp, push=push, prep=prep, force=force)


def _export_failed(out: Cut, exc: Exception) -> None:
    """An export failure as the cut's outcome: exit 3 when ddflow refuses (a hand-edited
    file), exit 2 when it could not run (git, the log)."""
    out.ok = False
    out.reason = str(exc)
    from .export.query import EXIT_REFUSED

    if getattr(exc, "code", 0) == EXIT_REFUSED:
        out.refused = True
    else:
        out.unavailable = True


def _prepare_changelog(repo, cfg, out: Cut, vp: VersionPlan, *, force: bool, dry_run: bool):
    """The rendered changelog for the cut, or None after recording why not on ``out``.
    The version's section also becomes the tag message (the request body in pr mode, where
    `pr sync` writes the tag's message later)."""
    from . import changelog_cut as CC
    from .export.query import ExportError

    try:
        prep = CC.prepare(repo, cfg, version=vp.next, ref=vp.ref, fallback_notes=vp.notes)
        if dry_run:
            prep.apply(W.repo_root(repo), force=force, dry=True)
    except ExportError as exc:
        _export_failed(out, exc)
        return None
    vp.notes = prep.notes
    if dry_run:
        out.steps.append(f"dry run: would write {prep.path} ({prep.mode})")
    return prep


def _write_changelog(repo, cfg, out: Cut, branch: str, prep, version: str, *, force: bool) -> bool:
    """Commit the prepared changelog on ``branch``; False (with the reason on ``out``) if not."""
    from . import changelog_cut as CC
    from .export.query import ExportError

    try:
        action = CC.commit_on(
            repo, cfg, branch, prep, force=force, message=f"docs: changelog for {version}"
        )
    except ExportError as exc:
        _export_failed(out, exc)
        return False
    out.changelog = prep.path
    out.steps.append(f"{action} {prep.path} on {branch}")
    return True


def _cut_gitflow(
    repo: Path,
    cfg: Config,
    log: EventLog,
    out: Cut,
    vp: VersionPlan,
    *,
    push: bool,
    prep=None,
    force: bool = False,
) -> Cut:
    remote = cfg.flow.remote
    prod = F.production(cfg, W.default_branch(repo))
    rel = f"{cfg.flow.release_prefix}{vp.next}"
    mk = W.git(repo, "branch", rel, vp.ref)
    if not mk.ok:
        out.refused, out.reason = True, f"could not create {rel}: {mk.err}"
        return out
    out.steps.append(f"created {rel} from {vp.ref}")
    if prep and not _write_changelog(repo, cfg, out, rel, prep, vp.next, force=force):
        W.git(repo, "branch", "-D", rel)  # unmade, as a failed push unmakes it
        return out
    if cfg.flow.integration == "pr":
        return _open_release(repo, cfg, log, out, vp, rel=rel, prod=prod)
    r = W.merge_into(repo, cfg, prod, rel, message=f"release {vp.next}")
    if not r.ok:
        out.refused, out.reason = True, f"merging {rel} into {prod} failed: {r.err or r.out}"
        return out
    out.steps.append(f"merged {rel} into {prod}")
    out.sha = W.rev(repo, prod)
    done = _tag_and_push(repo, cfg, log, out, vp, branch=prod, push=False)
    if not done.ok:
        return done
    back = W.merge_into(
        repo, cfg, cfg.flow.develop_branch, out.tag, message=f"back-merge {out.tag}"
    )
    if not back.ok:
        out.ok = False
        out.refused = True
        out.reason = (
            f"{out.tag} is tagged on {prod}, but merging it back into "
            f"{cfg.flow.develop_branch} failed: {back.err or back.out}. Resolve by hand; "
            f"until then develop's next version is computed from the previous tag."
        )
        return out
    out.steps.append(f"merged {out.tag} back into {cfg.flow.develop_branch}")
    W.git(repo, "branch", "-d", rel)
    if push:
        for ref in (prod, cfg.flow.develop_branch, out.tag):
            p = W.git(repo, "push", remote, ref)
            if not p.ok:
                out.reason = f"pushing {ref} failed: {p.err}"
                return out
        out.pushed = True
        out.steps.append(f"pushed {prod}, {cfg.flow.develop_branch} and {out.tag} to {remote}")
    return out


def _open_release(
    repo: Path, cfg: Config, log: EventLog, out: Cut, vp: VersionPlan, *, rel: str, prod: str
) -> Cut:
    remote = cfg.flow.remote
    pushed = W.push(repo, remote, rel)
    if not pushed.ok:
        # Unmade, so the next cut of this version is not refused by our own leftover.
        W.git(repo, "branch", "-D", rel)
        out.refused, out.reason = True, f"push of {rel} rejected: {pushed.err}"
        return out
    try:
        forge = FG.detect(repo, cfg)
        info = forge.find(rel) or forge.create(
            head=rel,
            base=prod,
            title=f"Release {vp.next}",
            body=vp.notes,
            draft=False,
            labels=list(cfg.flow.pr_labels),
            reviewers=list(cfg.flow.pr_reviewers),
        )
    except FG.ForgeUnavailable as exc:
        W.git(repo, "branch", "-D", rel)
        out.unavailable, out.reason = True, str(exc)
        return out
    except FG.ForgeError as exc:
        W.git(repo, "branch", "-D", rel)
        out.refused, out.reason = True, str(exc)
        return out
    log.append(
        "release.opened",
        vp.next,
        {"branch": rel, "number": info.number, "url": info.url, "base": prod, "forge": forge.name},
    )
    out.ok, out.url = True, info.url
    out.steps.append(f"opened {info.url} into {prod}; `pr sync` tags it once merged")
    return out


def _tag_and_push(
    repo: Path, cfg: Config, log: EventLog, out: Cut, vp: VersionPlan, *, branch: str, push: bool
) -> Cut:
    t = W.tag(repo, out.tag, out.sha, vp.notes)
    if not t.ok:
        out.refused, out.reason = True, f"could not tag {out.sha[:12]} as {out.tag}: {t.err}"
        return out
    out.steps.append(f"tagged {out.sha[:12]} as {out.tag}")
    if push:
        p = W.git(repo, "push", cfg.flow.remote, f"refs/tags/{out.tag}")
        if p.ok:
            out.pushed = True
            out.steps.append(f"pushed {out.tag} to {cfg.flow.remote}")
        else:
            out.reason = f"tagged locally, but the push failed: {p.err}"
    log.append(
        "release.tagged",
        vp.next,
        {
            "version": vp.next,
            "tag": out.tag,
            "sha": out.sha,
            "branch": branch,
            "items": vp.items,
            "pushed": out.pushed,
            "line": vp.line,
        },
    )
    out.ok = True
    return out


def _sync_releases(
    repo: Path, cfg: Config, log: EventLog, forge: FG.Forge, rep: SyncReport
) -> None:
    """Tag a gitflow release request once a person merged it, and send it back to develop."""
    st = _state(log)
    for ver, pend in sorted(st.pending_releases.items()):
        rep.checked += 1
        try:
            info = forge.view(int(pend.get("number") or 0))
        except (FG.ForgeUnavailable, FG.ForgeError) as exc:
            rep.unavailable.append(f"release {ver}: {exc}")
            continue
        if info.state == "closed":
            # Recorded, so the version is free again and the request is not re-polled
            # forever. The release branch is left for a person: it may hold fixes.
            log.append("release.closed", ver, {"url": info.url, "branch": pend.get("branch", "")})
            rep.changes.append(
                Change(
                    f"release {ver}", "closed", "closed without merging; version freed", info.url
                )
            )
            continue
        if info.state != "merged":
            rep.waiting.append(
                {
                    "id": f"release {ver}",
                    "url": info.url,
                    "review": info.review,
                    "checks": info.checks,
                    "base": info.base,
                }
            )
            continue
        W.fetch(repo, cfg.flow.remote, info.base)
        sha = info.merge_sha or info.head_sha
        if not W.rev(repo, sha):
            rep.unavailable.append(
                f"release {ver}: merge commit {sha[:12]} is not in the local clone after a fetch"
            )
            continue
        vp = VersionPlan(ref=info.base, next=ver, notes=f"Release {ver}\n\n{info.url}\n")
        vp.items = [
            i.id
            for i in st.items.values()
            if i.state == DONE
            and reached(repo, cfg, i, sha)
            and i.id not in {x for r in st.releases for x in r.items}
        ]
        c = Cut(version=ver, tag=f"{cfg.flow.tag_prefix}{ver}", sha=sha)
        c = _tag_and_push(repo, cfg, log, c, vp, branch=info.base, push=True)
        if not c.ok:
            rep.refused.append(f"release {ver}: {c.reason}")
            continue
        rep.changes.append(
            Change(
                f"release {ver}",
                "tagged",
                f"{c.tag} at {sha[:12]}" + ("" if c.pushed else f" (NOT pushed: {c.reason})"),
                info.url,
            )
        )
        try:
            # FROM production, not from the release branch: the tag sits on the merge
            # commit production made, and only merging THAT makes the tag reachable from
            # develop. Back-merging the release branch leaves develop computing its next
            # version from the previous tag and counting this release's commits twice.
            back = forge.create(
                head=info.base,
                base=cfg.flow.develop_branch,
                title=f"Back-merge release {ver} into {cfg.flow.develop_branch}",
                body=f"Brings {c.tag} into {cfg.flow.develop_branch}, so the next version is computed from a tag develop contains.",
                draft=False,
                labels=list(cfg.flow.pr_labels),
                reviewers=list(cfg.flow.pr_reviewers),
            )
            rep.changes.append(
                Change(f"release {ver}", "back_merge", f"into {cfg.flow.develop_branch}", back.url)
            )
        except (FG.ForgeError, FG.ForgeUnavailable) as exc:
            rep.refused.append(f"release {ver}: back-merge request could not be opened: {exc}")
