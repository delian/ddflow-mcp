"""PR flow: which commit of an item's landing a ref contains.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ...config import Config
from ...core.model import Item
from ...infra import git as GIT
from ...infra import worktree as W


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
    log = GIT.run(repo, "log", "-p", "--no-ext-diff", "--no-merges", *log_args, binary=True)
    if not log.ok or not log.out_bytes:
        return {}
    ids = GIT.run(repo, "patch-id", "--stable", input=log.out_bytes)
    out: dict[str, str] = {}
    for line in ids.out.splitlines() if ids.ok else []:
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
