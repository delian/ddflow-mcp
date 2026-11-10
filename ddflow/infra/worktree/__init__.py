"""Git worktree lifecycle — isolation that survives a crash.

One task, one worktree, one branch. The isolation is not about tidiness: two agents
sharing a working tree destroy each other, because ``git reset --hard``, ``git clean``
and ``git checkout`` are whole-tree operations and an agent has no way to scope them to
its own files.

Three rules encoded here, each from a failure that actually happened somewhere:

1. **Never switch the primary checkout's branch.** A checkout under a live session
   swaps the files beneath it. Worktrees are created FROM the primary and merged INTO
   it while it stays put; ``merge`` runs without any preceding checkout.
2. **Never remove a worktree that has unmerged work.** ``remove`` refuses on a dirty
   or ahead tree unless forced, because "the task is done" and "the tree is empty" are
   different claims and only the second one licenses deletion.
3. **Sync before you start, not at the end.** A branch that diverges for a week
   becomes unmergeable; the merge everyone postpones is the one that fails.

This package is the former ``ddflow/infra/worktree.py``, split into submodules that stay
under 30k chars each (rule r-small-files). The public API is unchanged: every name the
old module exported is re-exported here, so ``from ddflow.infra import worktree as W``
and ``from ddflow.infra.worktree import remove`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. The mutability the test-suite relies on is that of the primitives the split
routes through this module — ``monkeypatch.setattr(W, "git", ...)`` or ``("time", ...)``
re-binds the name on THIS module, and the submodules reach those primitives through it
(``_W.git``, ``_W.time``) rather than holding their own copies, because a commit hook
would otherwise read an eager ``from ._x import ...`` here as a latent parent<->child
cycle. Cross-submodule calls to the pure helpers (``repo_root``, ``list_worktrees``,
``checked_out_at``, ``_count``) bind their sibling-local names at import and are NOT
re-routed through ``_W``: they are not monkeypatched to change behaviour inside the
submodules, only at direct call sites.
"""

from __future__ import annotations

import shutil as shutil
import time as time

from ...core import _reexport
from .. import git as G

#: The one git runner lives in `infra.git`; these names stay importable from here.
git = G.run
git_paths = G.git_paths
status = G.status
GitError = G.GitError
GitResult = G.GitResult

# One implementation, in `core` -- the branch-name rules there build on it.
from ...core.flow import safe_name as safe_name  # noqa: E402

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # _locate
    "Scratch": "_locate",
    "Worktree": "_locate",
    "current": "_locate",
    "default_branch": "_locate",
    "list_worktrees": "_locate",
    "repo_root": "_locate",
    "scratch_tree": "_locate",
    "_ignore_inside": "_locate",
    # _local
    "SKIP_ABSENT": "_local",
    "SKIP_FAILED": "_local",
    "SKIP_OUTSIDE": "_local",
    "SKIP_PRESENT": "_local",
    "SKIP_TRACKED": "_local",
    "copy_local_files": "_local",
    "copy_local_files_report": "_local",
    "tracks_local_file": "_local",
    # _state
    "CACHES": "_state",
    "TreeWork": "_state",
    "UNREADABLE": "_state",
    "_count": "_state",
    "ahead": "_state",
    "behind": "_state",
    "dirty": "_state",
    "is_cache": "_state",
    "sync": "_state",
    "tree_work": "_state",
    "unreadable": "_state",
    # _mergecore
    "GIT_REFUSED": "_mergecore",
    "_merge_retrying_stash": "_mergecore",
    "_overwritten": "_mergecore",
    "_stash_failed": "_mergecore",
    # _merge
    "SQUASH_OF": "_merge",
    "_NAMED": "_merge",
    "_abandon_merge": "_merge",
    "_merge_here": "_merge",
    "checked_out_at": "_merge",
    "commit": "_merge",
    "head_sha": "_merge",
    "merge": "_merge",
    "merge_into": "_merge",
    "merging": "_merge",
    "remove": "_merge",
    # _create
    "create": "_create",
    "isolate_venv": "_create",
    # _diff
    "_DIFF_PREFIXES": "_diff",
    "_diff_text": "_diff",
    "_status_paths": "_diff",
    "capture_diff": "_diff",
    "diff_covers_everything": "_diff",
    "untracked_files": "_diff",
    # _paths
    "absolutise": "_paths",
    "branch_exists": "_paths",
    "branches": "_paths",
    "is_merged": "_paths",
    "load_path": "_paths",
    "repo_relative": "_paths",
    "store_path": "_paths",
    # _remote
    "apply_3way": "_remote",
    "fetch": "_remote",
    "log_messages": "_remote",
    "push": "_remote",
    "reachable_tag": "_remote",
    "rev": "_remote",
    "tag": "_remote",
    "version_tags": "_remote",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
