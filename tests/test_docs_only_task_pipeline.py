"""A task that writes only documents runs the doc pipeline (bug B-research-task-cannot-complete-docs-only).

A research or documentation task made through the MCP (`task_add` has no kind) is a `task`, so it
ran the code pipeline: `implement` and `unit_tests` are required, a skip does not satisfy
them, and `complete` demanded a cross-family reviewer for work that changed no code.
"""

from __future__ import annotations

from ddflow.config import Config
from ddflow.core.model import Item
from ddflow.services import completion as CO
from ddflow.services.gates import pipeline_for
from ddflow.services.gates.kinds import runs_doc_pipeline


def _task(*globs: str, **kw) -> Item:
    return Item(id="X", kind="task", title="t", globs=list(globs), **kw)


def test_a_task_that_writes_only_docs_runs_the_doc_pipeline():
    cfg = Config()
    it = _task("docs/ddflow/research/x.md", "README.md")
    assert runs_doc_pipeline(it, cfg)
    assert pipeline_for(it, cfg) == list(cfg.gates.doc_pipeline)


def test_one_code_glob_keeps_the_task_pipeline():
    cfg = Config()
    it = _task("docs/ddflow/research/x.md", "ddflow/api/review.py")
    assert not runs_doc_pipeline(it, cfg)
    assert pipeline_for(it, cfg) == list(cfg.gates.task_pipeline)


def test_a_task_with_no_globs_keeps_the_task_pipeline():
    cfg = Config()
    assert pipeline_for(_task(), cfg) == list(cfg.gates.task_pipeline)


def test_the_knob_turns_it_off():
    cfg = Config()
    cfg.gates.doc_globs = []
    it = _task("docs/x.md")
    assert not runs_doc_pipeline(it, cfg)
    assert pipeline_for(it, cfg) == list(cfg.gates.task_pipeline)


def test_a_promotion_and_a_phase_are_never_docs_only():
    cfg = Config()
    assert pipeline_for(_task("docs/x.md", promote_to="prod"), cfg) == list(
        cfg.gates.promotion_pipeline
    )
    phase = Item(id="P", kind="phase", title="p", globs=["docs/x.md"])
    assert pipeline_for(phase, cfg) == list(cfg.gates.phase_pipeline)


def test_a_docs_only_task_needs_no_independent_reviewer():
    cfg = Config()
    docs = _task("docs/x.md")
    code = _task("ddflow/x.py")
    assert not CO.needs_independent_reviewer(docs, cfg)
    assert CO.needs_independent_reviewer(code, cfg)


def test_a_docs_only_task_completes_with_the_doc_gates_alone(repo):
    """End to end: the doc pipeline's gates, no unit_tests, no independent reviewer."""
    from conftest import run_cli

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "D1", "--title", "write a note", "--globs", "docs/note.md")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("# note\n")
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        code, out, err = run_cli(
            repo, "gate", "record", "D1", gate, "--outcome", "passed", "--evidence", "ran it"
        )
    code, out, err = run_cli(repo, "complete", "D1", "--model", "claude-sonnet-5-5")
    text = out + err
    assert "unit_tests" not in text and "independence" not in text, text
