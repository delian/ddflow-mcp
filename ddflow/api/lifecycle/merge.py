"""`merge`: landing an item's branch, and what it refreshes after.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import flow as F
from ...core import outcome as O
from ...core.schedule import path_in_glob
from ...infra import worktree as W
from ...services import changes as CH
from ...services import flow as FS
from ...services import gates as G
from ...services.cleanup import dispose_tree, record_item_removed  # noqa: F401  (re-exported)
from ...services.enforce import SELF_MANAGED
from ...services.export import refresh as RF
from ...services.gates import measured as GM
from .._base import _load
from ..ci import check_after_merge
from ._common import _require
from .claim import callers_tree


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
    it = _require(st, item, "worktree.merged")
    if isinstance(it, O.Outcome):
        return it
    target = FS.target(repo, cfg, it, st)
    borrowed = not it.worktree  # claimed --no-worktree: land the branch it was worked on
    source = _what_to_land(repo, cfg, st, it, target, branch, called_from)
    if isinstance(source, O.Outcome):
        return source
    wt, dirty, outside = source
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
    refreshed = {} if borrowed else _refresh_documents(repo, cfg, wt)
    branch_head = W.rev(repo, wt.branch) if borrowed else W.head_sha(wt.path)
    landed_before = W.rev(repo, wt.base)
    r = W.merge(repo, cfg, wt, message=message or f"merge {item}: {it.title}")
    if not r.ok:
        # A merge that was tried and failed is a merge-gate outcome: the log-derived
        # `merge_failure_rate` flow signal counts it, and without this it saw successes only.
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
        out = O.Outcome(
            kind="worktree.merged",
            data={"id": item, "sha": "", "dirty": []},
            exit=O.REFUSED if r.code == W.GIT_REFUSED else O.FAIL,
            reason=r.err or r.out,
        )
        return out
    # What the base now points at: the landing. Falls back to the branch head only if
    # the base cannot be read back, which a merge that just succeeded makes unlikely.
    landed_after = W.rev(repo, wt.base)
    sha = landed_after or branch_head
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
            # Landed from a branch the item does not own (claimed --no-worktree).
            **({"borrowed": True, **_scope_fields(outside, listed=True)} if borrowed else {}),
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
    human_gate = GM.record_merge(
        log,
        cfg,
        repo,
        it,
        "passed",
        gates=G.load_gates(repo, cfg),
        tree=None if borrowed else wt.path,
        evidence={"sha": sha, "branch_head": branch_head, "branch": wt.branch},
    )
    removed_tree, kept_reason = _dispose_tree(
        repo, cfg, log, it, wt, borrowed=borrowed, keep=keep, callers=(called_from, shell_cwd)
    )
    if back_failed:
        kept_reason = "; ".join(
            filter(None, [kept_reason, "back-merge FAILED into " + ", ".join(back_failed)])
        )

    ci_after = check_after_merge(repo, sha=sha, item=item, agent=agent)
    return O.ok(
        "worktree.merged",
        id=item,
        **({"ci": ci_after} if ci_after else {}),
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
        **({"export_refresh": refreshed} if refreshed else {}),
    )


def _refresh_documents(repo: Path, cfg, wt: W.Worktree) -> dict[str, Any]:
    """`[export].refresh = merge`: regenerate the selected whole-file documents into the
    item's branch and commit them there, so they land in the merge. Never fatal: a document
    that cannot be refreshed is reported (``problems``) and the merge goes ahead.

    Empty when nothing is selected for ``merge`` (the default: refresh is off).
    """

    r = RF.refresh_selected(repo, "merge", root=wt.path, cfg=cfg)
    if not r.outcomes:
        return {}
    data = r.data()
    paths = r.changed
    if paths:
        add = W.git(wt.path, "add", "--", *paths)
        commit = (
            W.git(
                wt.path,
                "commit",
                "-q",
                "--no-verify",
                "-m",
                "refresh generated documents",
                "--",
                *paths,
            )
            if add.ok
            else add
        )
        if not commit.ok:
            W.git(wt.path, "reset", "-q", "HEAD", "--", *paths)
            created = {o.path for o in r.outcomes if o.action == "created"}
            for rel in paths:
                if rel in created:  # absent before the refresh: nothing to restore, remove it
                    (wt.path / rel).unlink(missing_ok=True)
                elif W.git(wt.path, "cat-file", "-e", f"HEAD:{rel}").ok:
                    W.git(wt.path, "checkout", "--", rel)
            data["changed"] = []
            data["problems_note"] = (
                f"could not commit the refreshed documents: {commit.err or commit.out}"
            )
    data["summary"] = r.summary() + (
        f"; {data['problems_note']}" if "problems_note" in data else ""
    )
    return data


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
    if _stands_in(wt.path, *callers):
        return False, (
            f"worktree {wt.path} kept: you are standing in it, and removing it would leave "
            f"your shell in a deleted directory. cd {W.repo_root(repo)} -- `ddflow cleanup "
            f"--apply` removes the tree once {it.id} is complete."
        )
    gone = dispose_tree(repo, cfg, log, wt, item=it)
    return (True, "") if gone.removed else (False, gone.why)


def _stands_in(tree: Path, *wheres: Path | None) -> bool:
    """Is any of ``wheres`` the tree or inside it?"""
    try:
        root = tree.resolve()
    except OSError:
        return False
    for where in wheres:
        if where is None:
            continue
        try:
            here = Path(where).resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            return True
    return False


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


def _outside_globs(repo: Path, it, target: str, branch: str) -> list[str] | None:
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
    return [
        p
        for p in changed
        if not p.startswith(SELF_MANAGED) and not any(path_in_glob(p, g) for g in it.globs)
    ]


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
