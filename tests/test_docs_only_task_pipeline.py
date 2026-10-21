"""A task whose ACTUAL change is documents only runs the doc pipeline (bug B-research-task-cannot-complete-docs-only).

A research or documentation task made through the MCP (`task_add` has no kind) is a `task`, so it
ran the code pipeline: `unit_tests` is required, a skip does not satisfy it, and `complete`
demanded a cross-family reviewer for work that changed no code. The ACTUAL diff decides, never
the declared globs (B-na-required-gate).
"""

from __future__ import annotations

from conftest import run_cli
from test_na_required_gate import _state, _worktree_with_change

from ddflow.config import Config
from ddflow.core.model import Item
from ddflow.services import completion as CO
from ddflow.services import gates as G
from ddflow.services.docsonly import diff_is_docs_only


def _task(*globs: str, **kw) -> Item:
    return Item(id="X", kind="task", title="t", globs=list(globs), **kw)


def test_a_diff_of_documents_only_is_docs_only():
    cfg = Config()
    assert diff_is_docs_only(_task("src/**"), cfg, ["docs/a.md", "README.md", "docs/x/y.md"])


def test_one_code_path_keeps_the_task_pipeline():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/**"), cfg, ["docs/a.md", "ddflow/api/review.py"])


def test_no_readable_or_empty_diff_is_never_docs_only():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/a.md"), cfg, None)
    assert not diff_is_docs_only(_task("docs/a.md"), cfg, [])


def test_the_knob_turns_it_off():
    cfg = Config()
    cfg.gates.doc_globs = []
    assert not diff_is_docs_only(_task("docs/x.md"), cfg, ["docs/x.md"])


def test_a_promotion_and_a_phase_are_never_docs_only():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/x.md", promote_to="prod"), cfg, ["docs/x.md"])
    phase = Item(id="P", kind="phase", title="p", globs=["docs/x.md"])
    assert not diff_is_docs_only(phase, cfg, ["docs/x.md"])


def test_a_docs_only_change_needs_no_independent_reviewer():
    cfg = Config()
    assert not CO.needs_independent_reviewer(_task("src/**"), cfg, ["docs/x.md"])
    assert CO.needs_independent_reviewer(_task("docs/x.md"), cfg, ["ddflow/x.py"])
    assert CO.needs_independent_reviewer(
        _task("docs/x.md"), cfg, None
    )  # declared globs never decide


def test_declared_doc_globs_alone_keep_the_task_pipeline(repo):
    """Globs are agent-editable: a task declaring docs but changing code keeps `unit_tests`."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/x.md")
    _worktree_with_change(repo, "src/code.py")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert "unit_tests" in s.pipeline


def test_a_task_whose_diff_is_documents_runs_the_doc_pipeline(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    _worktree_with_change(repo, "docs/note.md")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline
    assert G.status(st, cfg, "T1", None).pipeline == list(cfg.gates.task_pipeline)


def test_complete_accepts_a_docs_only_change_with_the_doc_gates_alone(repo):
    """End to end through `complete`: the doc pipeline's gates, no unit_tests, no reviewer."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "write a note", "--globs", "docs/**")
    _worktree_with_change(repo, "docs/note.md")
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        code, out, err = run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "ran it",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (gate, out, err)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err


def test_complete_still_demands_unit_tests_for_a_code_change(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/**")
    _worktree_with_change(repo, "src/code.py")
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "x", agent="a1"
        )
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code != 0 and "unit_tests" in out + err, out + err
