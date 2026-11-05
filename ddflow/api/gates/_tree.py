"""Where an item's work lives: answering "which checkout holds ``it``'s tree".

``item_tree`` and friends, and the ``_at_merged_tree`` proof a checkout stands at the
tree an item merged in as.
"""

from __future__ import annotations

from pathlib import Path

from ...core.bookkeeping import STATE_EXCLUDE
from ...infra import worktree as W
from ..lifecycle import callers_tree


def _at_merged_tree(repo: Path, cfg, it, here: Path) -> bool:
    """Does the checkout at `here` stand AT the tree `it` merged in as?

    `git merge-base --is-ancestor <merged_sha> <base>` proves the merge is in the base's
    history, not WHERE the checked-out tree stands: a revert, a later merge onto the
    base, or a moved checkout keeps the ancestry while the checked-out tree is a
    different one, and a gate run there measures the wrong tree and records its outcome
    for `it`. All must hold: the ancestry, the checked-out HEAD's tree being the merged
    sha's tree where a cheap tree-sha comparison decides it -- `merge` records the sha at
    the branch-line commit and advances the base with its OWN bookkeeping commit (the
    event log), so a just-merged base sits one commit past the recorded sha with the
    merged content untouched: a checkout whose non-.ddflow content IS the merged tree's
    stands at it, measured with one pathspec-restricted diff -- and the working tree
    carrying no uncommitted changes (`HEAD^{tree}` sees
    only the committed tree, so a dirty primary at the merged HEAD is not the merged
    tree either) (the B430dae3a68 reviewer holefix, both rounds). An index entry the
    user marked `assume-unchanged` or `skip-worktree` lies to BOTH clean checks: under
    the mark `git status` and `git diff --quiet` keep seeing the committed content while
    the working file differs, so no checkout whose index carries a mark can be PROVEN
    to stand at the merged tree; a checkout whose mark scan itself fails is not provable
    either, and neither is admitted (the reviewers' fourth and fifth rounds).
    """

    if not it.merged_sha:
        return False
    base = cfg.worktree.base_ref or W.default_branch(repo)
    if not W.git(repo, "merge-base", "--is-ancestor", it.merged_sha, base).ok:
        return False
    if not W.git(here, "merge-base", "--is-ancestor", "HEAD", base).ok:
        return False
    head_tree = W.git(here, "rev-parse", "HEAD^{tree}")
    merged_tree = W.git(repo, "rev-parse", f"{it.merged_sha}^{{tree}}")
    if not (head_tree.ok and merged_tree.ok):
        return False
    if head_tree.out.strip() != merged_tree.out.strip():
        # `merge` records `merged_sha` at the branch-line commit and then advances the base
        # with its OWN bookkeeping commit (the event log), so a just-merged base sits one
        # commit past the recorded sha with the merged content untouched. That is still
        # the merged tree; content that moved on the base (a later commit, a revert,
        # another item's merge) is not -- the tree comparison admits only a base whose
        # non-.ddflow content IS the merged tree's.
        # HEAD of the checkout ASKED ABOUT (`here`), not the primary's: a kept worktree moved
        # off the merged work must not be admitted by the primary standing at it.
        moved = W.git(here, "diff", "--name-only", it.merged_sha, "HEAD", "--", ".", *STATE_EXCLUDE)
        if not moved.ok or moved.out.strip():
            return False
    marked = W.git(here, "ls-files", "-v")
    if not marked.ok or any(
        line and (line[0].islower() or line[0] in "Ss") for line in marked.out.splitlines()
    ):
        return False
    if not W.dirty(W.Worktree(item=it.id, path=here, branch="", base=""), untracked=False):
        if W.git(here, "diff", "--quiet").ok:
            return True
    return False


def item_tree(repo: Path, cfg, st, it, called_from: Path | None) -> tuple[Path | None, str]:
    """(where ``it``'s work is, or None; the other item whose tree the caller is in, or "").

    Its tree. For a TASK claimed without one, the linked worktree the caller stands in
    (B8be9373cf5: run from there, a gate still ran in the primary -- `main`'s suite as
    the change's pass) -- unless that tree is another open item's -- and from the primary
    None: with worktrees on, the primary's working tree is nobody's in particular, and
    any uncommitted edit there would be credited to the item. None is recorded, not
    guessed. The primary stays the answer for a phase (its gates test the merged
    result), for an item never claimed, and with worktrees off -- the switch for a lone
    agent that works in the primary -- and for a task whose merge removed its worktree
    (B430dae3a68): its work IS committed at the base's HEAD, so the primary is the
    honest post-merge home and a worktree gate genuinely re-runs there. The primary
    counts as that home only while it still STANDS at the merged tree (`_at_merged_tree`):
    `merged_sha` in the base's history does not make a reverted, moved-on or checked-out
    elsewhere primary the merged tree (the B430dae3a68 holefix). A KEPT worktree a merge
    left in place (remove_on_merge=false) counts by the same test: moved or dirtied off
    the merged work, it is not the item's tree either (the reviewers' third round).
    """

    if it.worktree:
        tree = W.load_path(repo, it.worktree)
        if not it.merged_sha or _at_merged_tree(repo, cfg, it, tree):
            return tree, ""
    if _at_merged_tree(repo, cfg, it, repo):
        return repo, ""
    if it.kind != "phase" and cfg.worktree.enabled:
        here, held = callers_tree(repo, cfg, st, it, called_from)
        if held:
            return None, held
        if here is not None:
            if it.merged_sha and not _at_merged_tree(repo, cfg, it, here.path):
                return None, ""
            return here.path, ""
        if it.lease is not None:  # claimed --no-worktree, and asked from the primary
            return None, ""
    if it.merged_sha and it.kind != "phase":
        # off the `_at_merged_tree` branch, no honest task tree stands for a merged
        # item: recording None over a moved/dirty fall-through is the honest no-tree.
        return None, ""
    return repo, ""


def _where_to_run(repo: Path, cfg, st, it, gdef, called_from: Path | None):
    """(the directory a command gate runs in, or None; whose tree the caller is in)."""
    return (repo, "") if gdef.cwd != "worktree" else item_tree(repo, cfg, st, it, called_from)


def _kept_tree(repo: Path, it) -> Path | None:
    """The item's worktree when a merge KEPT it (``--keep`` / ``[worktree].remove_on_merge
    = false``), else None.

    `it.worktree` is the PERSISTENT record: `merge` clears it on a `worktree.removed` event
    (the fold handler), so a non-empty `it.worktree` means the merge kept the tree and a
    removed one reads empty. The question is read off the record, not re-sniffed from the
    filesystem: a later foreign removal that never attributes a `worktree.removed` to the
    item (a manual `rm`, a force-removal with a different subject) leaves `it.worktree`
    set, and reading the directory's presence would say "the merge removed its worktree"
    for a tree the merge kept (B-gate-run-after-keep-merge)."""
    if not it.worktree:
        return None
    return W.load_path(repo, it.worktree)


def _no_tree_reason(repo: Path, it, item: str, gate: str, held: str, force: bool = False) -> tuple[str, str]:
    """(why a worktree gate has nowhere to run, the recorded outcome it leaves standing or "").
    A MERGED item's tree is gone (DX-gate-run-after-merge): a pre-merge pass is kept and
    `complete` uses it; without one the reason says to run gates before `ddflow merge`.
    A KEPT tree (B-gate-run-after-keep-merge) is not "removed", and the reason says so."""
    if it.merged_sha:
        sha = it.merged_sha[:12]
        prior = it.gate_outcome(gate)
        kept = _kept_tree(repo, it)
        disposition = (
            f"its worktree was KEPT at {kept}, but that tree does not stand at the merged "
            f"tree"
            if kept is not None
            else "the merge removed its worktree"
        )
        if prior == "passed" and force:
            return (
                f"{item} merged ({sha}) and {disposition}: --force asked for a fresh {gate} "
                f"measurement that cannot be taken, so the pre-merge pass is REPLACED by "
                f"unavailable (it stays in the log). Record the real outcome with "
                f"`ddflow gate record {item} {gate} --outcome passed --evidence ...`.",
                "",
            )
        if prior == "passed":
            return (
                f"{item} merged ({sha}) and {disposition}: the recorded {gate} outcome "
                f"(passed before the merge) stands and `complete` uses it.",
                prior,
            )
        return (
            f"{item} merged ({sha}) and {disposition}, so {gate} cannot run now; gates are "
            f"run before `ddflow merge {item}`. Recording UNAVAILABLE; if it did pass on the "
            f"merged tree, say so with `ddflow gate record {item} {gate} --outcome passed "
            f"--evidence ...`.",
            "",
        )
    why = (
        f"the worktree you are in belongs to {held}, not {item}: its suite is not "
        f"{item}'s. Run the gate from the worktree {item} is worked in."
        if held
        else f"{item} was claimed without a worktree, and the primary's working tree "
        f"is not its: run the gate from the worktree it is worked in, or set "
        f"[worktree].enabled = false if the primary is where you work."
    )
    return why + " Recording UNAVAILABLE.", ""
