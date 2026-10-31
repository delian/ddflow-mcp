"""Pull requests and version tags: landing work where merges need approval (RESEARCH R16).

The agent's loop in a PR-governed repository is the same loop as anywhere else -- claim,
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

This package is the former ``ddflow/services/flow.py``, split into submodules that stay
under 30k chars each (rule r-small-files). The public API is unchanged: every name the
old module exported is re-exported here, so ``from ddflow.services.flow import open_request``
and ``from ddflow.services import flow as FS`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. That keeps the layering ratchet (`test_no_mutually_importing_pair` would read
an eager ``from ._sync import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core import _reexport

#: every name the old module exported -> the submodule that defines it.
_SUBMODULE_OF = {
    # report
    "Change": "_report",
    "SyncReport": "_report",
    # core
    "_state": "_core",
    "target": "_core",
    "stacked_on": "_core",
    "fork_point": "_core",
    # reached
    "ONE_PARENT": "_reached",
    "_SQUASH_SEARCH": "_reached",
    "_line_patch_ids": "_reached",
    "_line_subjects": "_reached",
    "_patch_ids": "_reached",
    "_squash_on": "_reached",
    "reached": "_reached",
    # open
    "Opened": "_open",
    "open_request": "_open",
    "pre_merge_blockers": "_open",
    "pr_body": "_open",
    # threads
    "ThreadsReport": "_threads",
    "_already": "_threads",
    "review_threads": "_threads",
    # version
    "VersionPlan": "_version",
    "_ref": "_version",
    "_set_next": "_version",
    "plan_version": "_version",
    "release_notes": "_version",
    "release_source": "_version",
    # cut
    "Cut": "_cut",
    "_cut_gitflow": "_cut",
    "_cut_refusal": "_cut",
    "_export_failed": "_cut",
    "_note_changelog_commit_stays": "_cut",
    "_open_release": "_cut",
    "_plan_version_files": "_cut",
    "_prepare_changelog": "_cut",
    "_prepare_version_files": "_cut",
    "_sync_releases": "_cut",
    "_tag_and_push": "_cut",
    "_write_changelog": "_cut",
    "_write_version_files": "_cut",
    "cut": "_cut",
    # sync
    "_SINGLE_PARENT": "_sync",
    "_apply": "_sync",
    "_back_merge": "_sync",
    "_changed": "_sync",
    "_changed_but_queue": "_sync",
    "_diff_patch_id": "_sync",
    "_landed": "_sync",
    "_landing": "_sync",
    "_queue_detail": "_sync",
    "_rebase_start": "_sync",
    "_remote_tip": "_sync",
    "_remove_tree": "_sync",
    "_report_queue": "_sync",
    "_settle_merged": "_sync",
    "_stale_review": "_sync",
    "_sync_back_merges": "_sync",
    "_track_back_merge": "_sync",
    "_waiting": "_sync",
    "sync": "_sync",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
