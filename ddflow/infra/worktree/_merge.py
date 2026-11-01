"""Committing and merging a worktree's branch into the base, and removing the tree."""

from __future__ import annotations

import shutil
from pathlib import Path

from ...config import Config
from .. import git as G
from .. import worktree as _W
from ._locate import Worktree, _ignore_inside, list_worktrees, repo_root, scratch_tree
from ._mergecore import GIT_REFUSED, _merge_retrying_stash
from ._state import UNREADABLE, ahead, dirty, unreadable


def commit(
    wt: Worktree,
    message: str,
    paths: list[str] | None = None,
    *,
    trailers: dict[str, str] | None = None,
) -> G.GitResult:
    """Commit with explicit paths and machine-readable trailers.

    Explicit paths, never ``-A``: a parallel agent's unrelated file staged into your
    commit is a corruption that is very hard to notice and very hard to undo. The
    ``Item:`` trailer is what lets an audit reconcile commits against the queue with
    ``git log --format='%(trailers:key=Item)'`` instead of parsing prose.
    """
    if paths:
        _W.git(wt.path, "add", "--", *paths)
    else:
        _W.git(wt.path, "add", "-u")
    body = message
    tr = dict(trailers or {})
    tr.setdefault("Item", wt.item)
    body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in tr.items())
    return _W.git(wt.path, "commit", "-m", body)


def checked_out_at(repo: Path, branch: str) -> Path | None:
    """The worktree (primary or linked) that has ``branch`` checked out, if any."""
    for entry in list_worktrees(repo):
        if entry.get("branch", "") == f"refs/heads/{branch}":
            return Path(entry["worktree"])
    return None


def merge(repo: Path, cfg: Config, wt: Worktree, *, message: str = "") -> G.GitResult:
    """Merge the task branch into base, never switching any checkout's branch."""
    return merge_into(repo, cfg, wt.base, wt.branch, message=message or f"merge {wt.item}")


def merge_into(repo: Path, cfg: Config, target: str, source: str, *, message: str) -> G.GitResult:
    """Merge ``source`` into ``target`` without switching any checkout's branch.

    Three cases, by where ``target`` is checked out:

    * **in the primary** — merge there, as ddflow always has.
    * **nowhere** — merge in a throwaway worktree of ``target``, then remove it. This
      is what gitflow needs: a hotfix lands on production AND develop, and the primary
      can be on at most one of them. It used to be refused ("check out X yourself"),
      which under gitflow meant every hotfix and every release stopped for a person.
      The throwaway tree touches no one's files; only the branch ref moves.
    * **in some other linked worktree** — refused. Merging there changes files under
      whoever is working in it, which is exactly the live-session hazard rule 1 exists
      for, and a throwaway tree is impossible because git allows a branch checked out
      in one place only.
    """
    root = repo_root(repo)
    if not _W.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{target}").ok:
        return G.GitResult(
            GIT_REFUSED,
            "",
            f"target branch {target!r} does not exist. Create it (for gitflow: "
            f"`git branch {target} <production>`), or set [flow] / [worktree].base_ref.",
            attempted=False,
        )
    where = checked_out_at(root, target)
    if where is not None and where.resolve() != root.resolve():
        return G.GitResult(
            GIT_REFUSED,
            "",
            (
                f"{target!r} is checked out in the worktree {where}. ddflow will NOT merge "
                f"into a tree someone may be working in, and git allows a branch checked "
                f"out in only one place. Finish or move that session, then re-run."
            ),
            attempted=False,
        )
    if where is not None:
        return _merge_here(root, cfg, source, message)
    wt_root = (root / cfg.worktree.root).resolve()
    wt_root.mkdir(parents=True, exist_ok=True)
    _ignore_inside(root, wt_root)
    with scratch_tree(root, target, prefix=f".merge-{_W.safe_name(target)}-", under=wt_root) as s:
        if s.path is None:
            return G.GitResult(
                s.add.code,
                s.add.out,
                f"could not stage a merge of {target}: {s.add.err}",
                attempted=False,
            )
        r = _merge_here(s.path, cfg, source, message)
        if not r.ok:
            _W.git(s.path, "merge", "--abort")
        return r


def _merge_here(tree: Path, cfg: Config, source: str, message: str) -> G.GitResult:
    # NO blanket dirty check here. An earlier version refused whenever the primary had
    # any modified tracked file, on the stated grounds that "a merge would mix them
    # into the result". That premise is FALSE, and a probe says so: merging with an
    # unrelated file dirty succeeds, the local edit does NOT enter the merge commit,
    # and it stays uncommitted afterwards. Git refuses precisely and only when the
    # merge would OVERWRITE a locally-modified file, and it names those files exactly.
    # Duplicating that judgement more crudely only refused safe merges -- including,
    # routinely, a merge blocked by ddflow's own freshly-written config.
    args = ["merge"]
    if cfg.worktree.merge_strategy == "no-ff":
        args.append("--no-ff")
    elif cfg.worktree.merge_strategy == "ff-only":
        args.append("--ff-only")
    elif cfg.worktree.merge_strategy == "squash":
        args.append("--squash")
    args += ["-m", message, source]
    was_merging = merging(tree)
    r = _merge_retrying_stash(tree, args)
    if not r.ok and was_merging is True:
        r = G.GitResult(r.code, r.out, r.err, attempted=False)  # someone else's merge is open
    # Not `not was_merging`: None ("could not tell" before) still gets the re-probe and
    # its warning, and only a merge that was not already in progress is ever aborted.
    if not r.ok and was_merging is not True:
        now = merging(tree)
        if now and was_merging is False:
            return _abandon_merge(tree, source, r)
        if now is None or now:
            return G.GitResult(
                r.code,
                r.out,
                f"{r.err or r.out}\n\n{tree} may be left mid-merge, and git could not say "
                f"whether by this merge, so it was not aborted: check `git -C {tree} status`, "
                f"and `git -C {tree} merge --abort` if it is this one.",
                # Indeterminate before the attempt: a merge found open now may be someone
                # else's, so this branch was not demonstrably judged.
                attempted=was_merging is not None,
            )
    if r.ok and cfg.worktree.merge_strategy == "squash":
        # `git merge --squash` STAGES the result and commits nothing -- `-m` is accepted
        # and ignored. So a squash "merge" used to report success with the work sitting
        # uncommitted in the primary, the task branch never reachable from base, and
        # `remove_on_merge` then refusing to delete a tree it believed unmerged.
        # Nothing to commit means the branch was already contained; that is success.
        if _W.git(tree, "diff", "--cached", "--quiet").ok:
            return r
        # Names what is squashed, so the lease hook can see the commit is exactly the
        # automatic merge (enforce.clean_merge_conclusion) -- as git's own GITHEAD_
        # does for a real merge.
        squashed = _W.git(tree, "rev-parse", "--verify", "--quiet", f"{source}^{{commit}}").out
        c = _W.git(tree, "commit", "-m", message, env={SQUASH_OF: squashed})
        if not c.ok:
            # Unstage it, as a failed merge is aborted: left staged, the next commit
            # anyone makes in this tree would carry it (B6926ec1ad9).
            undone = _W.git(tree, "reset", "--merge")
            state = (
                f"The squash was unstaged; {tree} is as it was."
                if undone.ok
                else f"Unstaging it ALSO failed ({undone.err}): run `git -C {tree} reset "
                f"--merge` by hand."
            )
            return G.GitResult(
                GIT_REFUSED, c.out, f"squash staged but commit failed: {c.err or c.out}\n\n{state}"
            )
        return c
    return r


#: Set by `ddflow merge` on the commit that concludes a squash: the squashed commit, so
#: the lease hook can tell the commit is exactly the automatic merge
#: (`enforce.clean_merge_conclusion`), as git's own GITHEAD_ does for a real merge.
SQUASH_OF = "DDFLOW_SQUASH_OF"


def merging(tree: Path) -> bool | None:
    """Is ``tree`` in the middle of a merge: MERGE_HEAD set, or a path left unmerged?

    None when git could not list unmerged paths: "could not tell" is not "no".
    """
    if _W.git(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD").ok:
        return True
    unmerged = G.unmerged(tree)
    return None if unmerged is None else bool(unmerged)


#: How many conflicting paths a refused merge names before summarising the rest.
_NAMED = 10


def _abandon_merge(tree: Path, source: str, r: G.GitResult) -> G.GitResult:
    """Abort the merge THIS call started and failed, and say why as a refusal.

    It used to be left as it stood: MERGE_HEAD set, the merge staged, a conflict
    unmerged -- in the PRIMARY, which agents may not touch. Every later merge by every
    agent then failed until a person ran `git merge --abort` (B6926ec1ad9). Only a merge
    this call began is aborted: one already in progress is someone's, and git refuses
    to start another over it anyway.
    """
    conflicts = G.unmerged(tree)
    aborted = _W.git(tree, "merge", "--abort")
    if not aborted.ok:  # a --squash conflict has no MERGE_HEAD for --abort to find
        aborted = _W.git(tree, "reset", "--merge")
    why = r.err or r.out
    if conflicts is None:
        head = f"merging {source} failed and git could not list the conflicting paths:\n{why}"
        fix = "Merge the base into your branch, in your worktree, and run merge again."
    elif conflicts:
        head = f"merging {source} conflicts in: " + ", ".join(conflicts[:_NAMED])
        if len(conflicts) > _NAMED:
            head += f" (and {len(conflicts) - _NAMED} more)"
        fix = (
            "Merge the base into your branch, in your worktree, resolve, commit, and "
            "run merge again."
        )
    else:
        head = f"merging {source} was refused before its commit:\n{why}"
        fix = "Fix what refused it, then run merge again."
    state = (
        f"The merge was aborted; {tree} is as it was."
        if aborted.ok
        else f"Aborting it ALSO failed ({aborted.err}): {tree} is left mid-merge. "
        f"Run `git -C {tree} merge --abort` by hand."
    )
    return G.GitResult(GIT_REFUSED, r.out, f"{head}\n\n{state} {fix}")


def head_sha(path: Path) -> str:
    r = _W.git(path, "rev-parse", "HEAD")
    return r.out if r.ok else ""


def remove(
    repo: Path, cfg: Config, wt: Worktree, *, force: bool = False, landed_tip: str = ""
) -> G.GitResult:
    """Remove a worktree, refusing to destroy unmerged work unless forced.

    ``landed_tip`` is the caller's proof that the commits ahead of the base, up to that
    commit, are not work (a landed item's merge-main/pin leftovers): the commit count is not
    a reason to refuse while HEAD is still that commit (a later one is work, and refuses),
    but uncommitted files still are, and git itself refuses them atomically with the removal
    (no ``--force``), so a file written after the check is not lost."""
    root = repo_root(repo)
    if not force:
        d, a = dirty(wt), ahead(wt)
        a = 0 if landed_tip and a > 0 and head_sha(wt.path) == landed_tip else a
        if unreadable(d) or a < 0:
            # Unknown is not clean: an unmeasured tree may hold anything (B028b11b4cb).
            why = d[0][len(UNREADABLE) :] if unreadable(d) else f"no commit count vs {wt.base}"
            return G.GitResult(
                2,
                "",
                (
                    f"refusing to remove {wt.path}: could not measure it ({why}). "
                    f"Inspect with `git -C {wt.path} status`; pass --force once you are certain."
                ),
            )
        if d or a > 0:
            return G.GitResult(
                2,
                "",
                (
                    f"refusing to remove {wt.path}: {len(d)} uncommitted file(s), "
                    f"{a} unmerged commit(s). Inspect with `git -C {wt.path} diff {wt.base}`; "
                    f"pass --force once you are certain."
                ),
            )
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(wt.path)]
    r = _W.git(root, *args)
    if not r.ok and wt.path.exists() and force:
        shutil.rmtree(wt.path, ignore_errors=True)
        _W.git(root, "worktree", "prune")
        return G.GitResult(0, "removed (pruned)", "")
    if r.ok:
        _W.git(root, "branch", "-d", wt.branch)
    return r
