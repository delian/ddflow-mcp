"""B-complete-ignores-diff-doc-pipeline: after `ddflow merge` of a docs-only task (worktree gone,
branch kept) `gate status` and `complete` must resolve the SAME pipeline: the doc pipeline."""

from __future__ import annotations

from conftest import pass_pipeline, run_cli
from test_na_required_gate import _state, _worktree_with_change

from ddflow.config import Config
from ddflow.services import completion as CO
from ddflow.services import gates as G


def test_a_merged_docs_only_task_has_one_pipeline_for_status_and_complete(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "note", "--globs", "docs/**")
    _worktree_with_change(repo, "docs/note.md")
    pass_pipeline(repo, "T1", omit=("merge",))
    code, out, err = run_cli(repo, "merge", "T1", agent="a1")
    assert code == 0, out + err
    cfg = Config.load(repo)
    st = _state(repo)
    it = st.items["T1"]
    assert not it.worktree  # the tree is gone: the diff must come from what landed
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, it))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline
    code, out, err = run_cli(repo, "gate", "status", "T1", agent="a1")  # the CLI wrapper itself
    assert code == 0 and "COMPLETE" in out and "unit_tests" not in out, out + err
    v = CO.verdict(st, cfg, "T1", repo=repo, model="claude-sonnet-5-5")
    assert v.blockers == [], v.blockers
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err
