"""Bf0a1039f9f: `ddflow_complete` (the MCP tool) judged an item's kind and pipeline from a
fold cached by the handshake's `load_scope`. After `ddflow update --kind doc` the cached
pipeline still named `unit_tests` -- a REQUIRED gate only the old pipeline ran -- so the
in-process completion kept refusing "required gate(s) not passed: unit_tests (skipped)",
while `ddflow complete` from the shell, a fresh process every time, read the new kind and
completed. A completion must re-read the current kind/gate state like the CLI does."""

from __future__ import annotations

from conftest import pass_pipeline, run_cli

from ddflow.api._base import _load, load_scope
from ddflow.api.lifecycle import complete
from ddflow.core import outcome as O


def test_complete_rereads_kind_after_an_update_appended_to_the_log(repo):
    # `unit_tests` is REQUIRED and in the TASK pipeline a `research` item runs, but not in
    # the DOC pipeline `--kind doc` switches it to. The item has no worktree and no branch,
    # so its diff is unreadable and cannot auto-detect the doc pipeline: only the kind
    # decides -- the exact state the completion must re-read, not reuse from a cache.
    run_cli(repo, "init")
    run_cli(
        repo, "task", "add", "T1", "--title", "note", "--kind", "research", "--globs", "docs/**"
    )
    pass_pipeline(repo, "T1", omit=("unit_tests",))
    run_cli(repo, "gate", "skip", "T1", "unit_tests", "--reason", "docs-only research note")

    with load_scope():
        # Fold ONCE while the item is still research-kind: the stale cache the bug reports.
        _load(repo, "a1")
        # A separate process appends the kind change to the log, invisible to that fold.
        code, out, err = run_cli(repo, "update", "T1", "--kind", "doc")
        assert code == 0, out + err
        # The in-process completion (what `ddflow_complete` calls) must re-read the log:
        # unit_tests is no longer a gate of a doc item, so it completes without --force.
        res = complete(repo, "T1", model="claude-sonnet-5-5", agent="a1")

    assert res.exit == O.OK, res.reason
