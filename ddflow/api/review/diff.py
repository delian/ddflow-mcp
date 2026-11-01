"""Diff acquisition: what a review actually sends -- a commit, a branch, an item's tree, or the delta since a reviewed head."""

from __future__ import annotations

from pathlib import Path

from ...core.bookkeeping import EVENTS_EXCLUDE
from ...infra import worktree as W
from ...services.enforce import SELF_MANAGED
from ...services.review_noise import registered_submodules, without_stray_gitlinks
from ..lifecycle import callers_tree

_NOISE_PATHSPEC = ("--", ".", *EVENTS_EXCLUDE)


_NOISE_PREFIXES = (".ddflow/events/",)


def commit_diff(repo: Path, sha: str) -> tuple[str, str]:
    """(diff, how) for ONE landed commit, against its first parent.

    The review the source projects run after every commit (`roborev review <sha>`) is of
    the commit, not of a branch: by then the branch is merged and gone, and HEAD may be a
    merge whose own diff is empty. First parent, so a merge commit's review is of what
    the merge brought in. A root commit is diffed against the empty tree.
    """
    r = W.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if not r.ok:
        return "", f"commit {sha!r} not found"
    full = r.out
    # Whether the commit HAS a parent comes from its own header, which a shallow clone
    # keeps; whether the parent is PRESENT is a separate question. Conflating them
    # reviewed a shallow-cloned commit against the empty tree -- the whole repository
    # presented as that commit's change (roborev 830).
    header = W.git(repo, "cat-file", "-p", full)
    parents = [ln.split()[1] for ln in header.out.splitlines() if ln.startswith("parent ")]
    if not parents:
        base = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # the empty tree: a root commit
    else:
        base = parents[0]
        if not W.git(repo, "cat-file", "-e", f"{base}^{{commit}}").ok:
            return "", (
                f"commit {full[:12]}'s parent {base[:12]} is not in this clone (shallow?); "
                f"its change cannot be computed here"
            )
    d = W.git(repo, "diff", "--no-color", base, full, *_NOISE_PATHSPEC)
    out = without_stray_gitlinks(d.out, registered_submodules(repo, base, full)) if d.ok else ""
    return (out + "\n") if out else "", f"commit {full[:12]} vs its first parent"


SHOWN_UNTRACKED = 10


def _base_after_rewrite(repo: Path, it, base: str, tip: str) -> tuple[str, str]:
    """(base, warning): the base to diff against, and why it is not the live `base` ref.

    A history rewrite (a secret scrub) replaces the commit the item forked from, so
    `git merge-base base HEAD` drops to a pre-scrub ancestor and a plain diff sends the
    scrub's reverse changes as the item's (B05dce361fd: a 21 MB reversed diff to the
    reviewer). The item recorded the fork sha (`it.base_sha`); once `base` no longer
    contains it, review against that sha instead -- the branch forked from it, so the
    diff is exactly the item's own work. While `base` still contains the fork sha (main
    merely moved forward) it is returned unchanged: the merge-base still excludes main's
    commits as always.

    The fork sha is trusted only while the reviewed `tip` still descends from it. A
    branch rebased onto the rewritten base shares recent history with `base` again and
    must NOT be diffed against the pre-rewrite fork, so those cases fall back to the live
    `base` (whose merge-base with `tip` is then, once more, the right fork).
    """
    if it is None or not getattr(it, "base_sha", ""):
        return base, ""
    if W.git(repo, "merge-base", "--is-ancestor", it.base_sha, base).ok:
        return base, ""
    # base was rewritten. Trust the fork only while the tip still descends from it AND has
    # not brought the rewritten base into itself: a rebased tip shares recent base history
    # again, and a merged tip contains base's new commits -- in both, `base...tip` is right
    # once more, exactly as before the rewrite.
    if not W.git(repo, "merge-base", "--is-ancestor", it.base_sha, tip).ok:
        return base, ""
    if W.git(repo, "merge-base", "--is-ancestor", base, tip).ok:
        return base, ""
    return it.base_sha, (
        f"{base} was history-rewritten since the item forked from {it.base_sha[:12]}: "
        "the reviewer sees only the item's own commits on top of that recorded base"
    )


def diff_for(
    repo: Path,
    cfg,
    st,
    item: str,
    base: str = "",
    branch: str = "",
    called_from: Path | None = None,
) -> tuple[str, str]:
    """(diff, how) for an item: its worktree branch vs base, plus tracked edits.

    Untracked files in an item's tree are NOT in the diff (B2bf4d38cc1): a draft left
    untracked was reviewed as part of the change. `how` names them -- "untracked, not
    reviewed" -- in the output and the recorded `diff_source`, for whoever triages a
    "no tests" finding; the reviewer itself does not see them. Commit what is to be
    reviewed.

    For an item, the item's work (B60de9a57ed): ``branch`` when named; else its tree;
    else its recorded branch (the tree is gone); else the branch checked out in the
    linked worktree the caller stands in, unless another open item holds that tree. An
    item CLAIMED without a worktree gets nothing more -- an empty diff, recorded
    UNAVAILABLE: the primary's working tree was the old fallback, and in a busy checkout
    it held other agents' uncommitted event logs, 43 KB of them, which the reviewer found
    nothing wrong with and which were recorded as this item's PASS. Any other uncommitted
    edit there would be credited the same way, so no path filter makes it the item's.
    Only an unclaimed item, or worktrees off, still reads the primary -- without ddflow's
    own bookkeeping.
    """
    base = base or cfg.worktree.base_ref or W.default_branch(repo)
    if not item:
        return _captured(repo, f"working tree in {repo}")
    it = st.items.get(item)
    wt_path = W.load_path(repo, it.worktree) if it and it.worktree else None
    if not branch and wt_path and wt_path.exists():
        base, warning = _base_after_rewrite(wt_path, it, base, "HEAD")
        diff, how = _tree_diff(wt_path, base)
        if warning:
            how += f" (WARNING: {warning})"
        return diff, how
    branch, chosen, early = _review_branch(repo, cfg, st, it, item, branch, called_from)
    if early is not None:
        return early
    if not branch:
        return _captured(
            repo,
            f"working tree in {repo}, ddflow's bookkeeping excluded -- for {item}'s work "
            f"on a branch, pass --branch <branch> or run review from its worktree",
            exclude=SELF_MANAGED,
        )
    base, warning = _base_after_rewrite(repo, it, base, branch)
    d = W.git(repo, "diff", "--no-color", f"{base}...{branch}", *_NOISE_PATHSPEC)
    how = f"{base}...{branch} ({chosen})"
    if warning:
        how += f" (WARNING: {warning})"
    if not d.ok:  # git could not say: an empty diff, recorded unavailable, with the reason
        return "", f"{how} could not be read: {d.err or d.out}"
    out = without_stray_gitlinks(d.out, registered_submodules(repo, base, branch))
    return (out + "\n") if out else "", how


def _tree_diff(wt_path: Path, base: str) -> tuple[str, str]:
    """(diff, how) of an item's worktree: the branch's commits plus TRACKED edits. An
    untracked file is a draft nobody committed (B2bf4d38cc1): it is named, not reviewed."""
    try:
        diff = W.capture_diff(wt_path, base, include_untracked=False, exclude=_NOISE_PREFIXES)
        diff = without_stray_gitlinks(diff, registered_submodules(wt_path, base, "HEAD"))
    except RuntimeError as exc:  # git could not say: an empty diff, recorded unavailable
        return "", f"{base}..HEAD in {wt_path} could not be read: {exc}"
    how = f"{base}..HEAD + tracked working-tree changes in {wt_path}"
    if untracked := W.untracked_files(wt_path):
        shown = ", ".join(untracked[:SHOWN_UNTRACKED]) + (
            f", ... ({len(untracked)} in all)" if len(untracked) > SHOWN_UNTRACKED else ""
        )
        how += f" (untracked, not reviewed: {shown})"
    if diff.strip():
        ok, missing = W.diff_covers_everything(wt_path, diff, ignore_untracked=True)
        if not ok:
            how += f" (WARNING: {len(missing)} changed path(s) absent from the diff)"
    return diff, how


def _review_branch(
    repo: Path, cfg, st, it, item: str, branch: str, called_from: Path | None
) -> tuple[str, str, tuple[str, str] | None]:
    """(branch, why, early): the branch to review when no tree answers, and the
    (diff, how) to return at once when none can be named."""
    chosen = "named with --branch" if branch else ""
    if not branch and it and it.branch:
        branch, chosen = it.branch, f"{item}'s branch (its tree is gone)"
    if not branch and it:
        here, held = callers_tree(repo, cfg, st, it, called_from)
        if held:
            return (
                branch,
                chosen,
                (
                    "",
                    f"the worktree you are in belongs to {held}, not {item}; pass {item}'s "
                    f"branch with --branch",
                ),
            )
        if here is not None and here.branch:
            branch, chosen = here.branch, f"checked out in {here.path}"
    if not branch and it and it.lease is not None and cfg.worktree.enabled:
        return (
            branch,
            chosen,
            (
                "",
                f"{item} was claimed without a worktree, and the primary's working tree is not "
                f"its: pass --branch <branch>, run review from the worktree it is worked in, "
                f"review what already landed with --commit <sha> (a research item's committed "
                f"doc) or --base <ref>, or set [worktree].enabled = false if the primary is "
                f"where you work",
            ),
        )
    return branch, chosen, None


def _captured(tree: Path, how: str, **kw) -> tuple[str, str]:
    """(`W.capture_diff` of ``tree``, ``how``); git failing is an empty diff whose ``how``
    says so -- the review then records UNAVAILABLE, never "nothing changed"."""
    try:
        return W.capture_diff(tree, **kw), how
    except RuntimeError as exc:
        return "", f"{how}: git could not read it ({exc})"
