"""The delta cut: what changed since the reviewed head, and the branch/tree diff behind it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...infra import worktree as W
from ...services.enforce import SELF_MANAGED
from ...services.review_noise import registered_submodules, without_stray_gitlinks
from .diff import _NOISE_PATHSPEC, _NOISE_PREFIXES, commit_diff, diff_for
from .records import _last_review, _prior_evidence

_DELTA_CUT_HOW = "the delta since "  # `diff_source` of a --chunk re-run cut from a delta


_NOTHING_CHANGED = "nothing changed since the reviewed head"


def _pick_diff(repo, cfg, st, it, a: dict[str, Any], delta: bool) -> tuple[str, str, str]:
    """(diff, how, why not): what this call reviews -- the delta, a `--chunk` re-run of a
    recorded delta (`_delta_cut`), a commit, or the item's whole change."""
    item, gate = a["item"], a["gate"]
    if delta:
        return _delta_scope(repo, it, gate, a["branch"], cfg.worktree.base_ref or "")
    if (
        a["chunks"]
        and not a["commit"]
        and not a["base"]
        and (found := _delta_cut(repo, cfg, it, a))
    ):
        return found
    if a["commit"]:
        return (*commit_diff(repo, a["commit"]), "")
    diff, how = diff_for(
        repo, cfg, st, item, a["base"], branch=a["branch"], called_from=a["called_from"]
    )
    return diff, how, ""


def _delta_cut(repo, cfg, it, a: dict[str, Any]) -> tuple[str, str, str] | None:
    """(diff, how, why not) a `--chunk` re-run of a recorded DELTA round is cut from, or None
    when the record is not a delta's.

    The chunk numbers of such a record mean something only in the delta it reviewed -- the
    commits since `delta_from` -- not in the item's whole change, so a partial delta
    (a chunk timed out) could never be completed (B00e6b5a540). The diff is cut afresh: if
    the branch moved since, its digest differs and `_rerun_scope` refuses it. A delta that
    cannot be produced is said so, not read as a different cut."""
    if not (it and a["item"]):
        return None
    start = str(_prior_evidence(it.gates.get(a["gate"])).get("delta_from") or "")
    if not start:
        return None
    try:
        diff = _delta_diff(repo, it, a["branch"], start, cfg.worktree.base_ref or "")
    except RuntimeError as exc:
        return (
            "",
            "",
            f"the delta since {start[:10]} could not be produced ({exc}); run the full review.",
        )
    if not diff.strip():
        return (
            "",
            "",
            f"nothing changed since {start[:10]}, the start of the recorded delta; run the full review.",
        )
    return diff, f"{_DELTA_CUT_HOW}{start[:10]}", ""


def _last_head(it, gate: str) -> str:
    """The head the gate's latest `ddflow review` covered, read from its history: a
    re-claim, `gate skip` or `gate record` replaces the gate's current evidence, and a
    delta must stay possible after any of them (the count survives them too)."""
    return str(_last_review(it, gate).get("reviewed_head") or "")


def _kind(repo, cfg, it, item, gate, chunks, delta, commit, base) -> str:
    """full | delta | chunk: what the round is. A full and a delta round both count
    against `[review].max_rounds`; only a full one is numbered (`round`).

    Judged by what a review COVERS, not by the flag that asked for it: `--delta` always
    is one; a `--commit` or `--base` review is one only when that ref can only be
    narrower than the item's whole diff -- at or after the head the gate's last review
    covered, or strictly inside the item's own line (after its merge-base with the base
    branch, up to the reviewed head). A ref that reaches back to the base branch or before can cover it all, and
    counts as a full round.
    """
    if chunks:
        return "chunk"
    if delta:
        return "delta"
    ref = commit or base
    head = _last_head(it, gate) if ref and item else ""
    if not head:
        return "full"
    if W.git(repo, "merge-base", "--is-ancestor", head, ref).ok:
        return "delta"
    start = W.git(repo, "merge-base", cfg.worktree.base_ref or W.default_branch(repo), head)
    inside = (
        start.ok
        and W.git(repo, "merge-base", "--is-ancestor", start.out, ref).ok
        and W.git(repo, "merge-base", "--is-ancestor", ref, head).ok
        and W.git(repo, "rev-parse", ref).out != W.git(repo, "rev-parse", start.out).out
    )
    return "delta" if inside else "full"


def _delta_scope(repo, it, gate, branch, base: str = "") -> tuple[str, str, str]:
    """(diff, how, why not) for `--delta`: the item's own changes since the recorded
    review's head -- never what a merge brought in from ``base`` (Bccf6d1aec7)."""
    head = _last_head(it, gate)
    if not head:
        return (
            "",
            "",
            f"--delta rechecks what changed since the head of the item's recorded {gate} "
            "review, and none is on record: run the full review first.",
        )
    try:
        diff = _delta_diff(repo, it, branch, head, base)
    except RuntimeError as exc:
        return "", "", f"the delta could not be produced ({exc}); `--full` reviews the whole diff."
    if not diff.strip():
        return (
            "",
            "",
            f"{_NOTHING_CHANGED} {head[:10]}. `--full` reviews the "
            "whole diff again (a full round).",
        )
    n = _commits_since(repo, it, branch, head, base)
    what = f"{n} commit{'' if n == 1 else 's'}" if n else "uncommitted changes"
    return (
        diff,
        f"delta review of {what} since {head[:10]}",
        "",
    )


def _commits_since(repo: Path, it, branch: str, head: str, base: str = "") -> int:
    """How many commits the item's tip has past `head` (0: only working-tree edits): its
    OWN, so the commits a merge brought in from ``base`` (default: the default branch)
    are not counted (Bccf6d1aec7)."""
    tip = _head_of(repo, it, branch, "")
    base = base or W.default_branch(repo)
    r = W.git(repo, "rev-list", "--count", f"{head}..{tip}", "--not", base) if tip else None
    return int(r.out) if r is not None and r.ok and r.out.strip().isdigit() else 0


def _delta_start(repo: Path, tip: str, head: str, base: str) -> str:
    """What a delta diffs the tip against: ``head``, the reviewed commit -- or, when the
    branch has merged ``base`` since (Bccf6d1aec7), ``head`` merged with what came in.
    The commits from ``base`` were reviewed as their own items, so only what the branch
    did on top of them is new: its own commits, and its side of the merge.

    What came in is every merge base of ``base`` and the tip that ``head`` lacks (one in
    a plain history, more after criss-cross merges). Each is merged onto ``head`` with
    ``git merge-tree --write-tree -X theirs``: where the two conflict the incoming side
    is kept, so the diff to the tip shows exactly where the item's result departs from
    the base's: a conflict it resolved its own way (overriding another item's change)
    is its decision and is reviewed; one where it took the base's side is not new code.
    Returns ``head`` when nothing came in, else the merged tree. When a merge cannot be
    written (a modify/delete or rename conflict, which ``-X theirs`` does not settle):
    with one incoming base, that commit -- the item's whole own change on top of it, and
    none of the base's; with several, ``head`` -- everything since the reviewed head, the
    pre-fix range, since no single one of them excludes the others' work. More than the
    item's own, never less."""
    found = W.git(repo, "merge-base", "--all", base, tip) if base and tip else None
    bases = found.out.split() if found is not None and found.ok else []
    came_in = sorted(
        b for b in bases if not W.git(repo, "merge-base", "--is-ancestor", b, head).ok
    )  # sorted: `merge-base --all` names them in no particular order
    start = head
    for i, commit in enumerate(came_in):
        merged = W.git(repo, "merge-tree", "--write-tree", "-X", "theirs", start, commit)
        tree = merged.out.splitlines()[0].strip() if merged.ok and merged.out.strip() else ""
        if not tree:
            return came_in[0] if len(came_in) == 1 else head
        if i == len(came_in) - 1:
            return tree
        # Another base to merge: the tree as a commit merge-tree can take (unreferenced,
        # like the tree itself; git's gc removes both).
        made = W.git(repo, "commit-tree", tree, "-p", start, "-p", commit, "-m", "delta base")
        if not made.ok or not made.out.strip():
            return head
        start = made.out.strip()
    return head


def _head_of(repo: Path, it, branch: str, commit: str) -> str:
    """The commit a review covered: what `--delta` later diffs from. "" when unknown."""

    wt = W.load_path(repo, it.worktree) if it and it.worktree else None
    where, ref = (
        (repo, commit)
        if commit
        else (repo, branch)
        if branch
        else (wt, "HEAD")
        if wt and wt.exists()
        else (repo, it.branch if it and it.branch else "HEAD")
    )
    r = W.git(where, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return r.out.strip() if r.ok else ""


def _delta_diff(repo: Path, it, branch: str, head: str, base: str = "") -> str:
    """What the item changed since `head`: its branch or tree against that commit --
    against `head` merged with ``base``'s incoming work when the branch merged ``base``
    since (`_delta_start`), so other items' commits are never sent (Bccf6d1aec7)."""

    base = base or W.default_branch(repo)
    wt = W.load_path(repo, it.worktree) if it and it.worktree else None
    if not branch and wt and wt.exists():
        return _delta_of_tree(wt, head, base)
    tip = branch or (it.branch if it and it.branch else "")
    if tip:
        return _delta_of_branch(repo, tip, head, base)
    return W.capture_diff(repo, head, include_untracked=False, exclude=SELF_MANAGED)


def _delta_of_tree(wt: Path, head: str, base: str) -> str:
    """The delta of an item's worktree: its commits and tracked edits since the start.
    Raises RuntimeError when a diff cannot be produced: never "nothing changed"."""
    start = _delta_start(wt, "HEAD", head, base)
    # `start..HEAD` plus the tracked edits: what a review sent, with a git failure kept.
    diff = W.capture_diff(wt, include_untracked=False, since=start, exclude=_NOISE_PREFIXES)
    return without_stray_gitlinks(diff, registered_submodules(wt, base, "HEAD", start, head))


def _delta_of_branch(repo: Path, tip: str, head: str, base: str) -> str:
    """The delta of a named branch. Raises RuntimeError when no diff can be produced: a
    delta that could not run is never "nothing changed"."""
    start = _delta_start(repo, tip, head, base)
    rng = (f"{head}...{tip}",) if start == head else (start, tip)
    d = W.git(repo, "diff", "--no-color", *rng, *_NOISE_PATHSPEC)
    if not d.ok:  # never a wider range than asked: that would send the base's work
        raise RuntimeError(f"git diff {' '.join(rng)} failed: {d.err or d.out}")
    out = without_stray_gitlinks(d.out, registered_submodules(repo, base, head, start, tip))
    return (out + "\n") if out else ""
