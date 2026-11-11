"""B-complete-records-no-sha: `complete` fills the sha from the merge event, so a merged
item completes "as <merge sha>" and the ledger records that sha, never an empty one
(`show --report` reads an empty sha as "completed ... as -").

The fill is `finish.complete`'s ``sha = sha or it.merged_sha`` (B9f8019c521): without it a
merged item records an empty sha even though `merge` had recorded its landing.
"""

from __future__ import annotations

from conftest import pass_pipeline, run_cli
from test_na_required_gate import _worktree_with_change

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import ledger as LG


def test_complete_of_a_merged_item_records_the_merge_sha(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "note", "--globs", "docs/**")
    _worktree_with_change(repo, "docs/note.md")
    pass_pipeline(repo, "T1", omit=("merge",))
    code, out, err = run_cli(repo, "merge", "T1", agent="a1")
    assert code == 0, out + err

    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err

    st = fold(EventLog(repo).read_all(), strict=False)
    it = st.items["T1"]
    led = LG.build(EventLog(repo).read_all(), "T1")
    assert it.merged_sha, "the merge recorded no landing sha to complete with"
    assert led["sha"] == it.merged_sha, f"completed with no merge sha: {led['sha']!r} != {it.merged_sha!r}"
