"""B267a6b0b5e: a phase's gate run (no globs) is superseded by any commit, event-only ones
included; a `.ddflow/events` commit (ddflow's own bookkeeping) must not supersede the whole
source tree a phase gate ran against."""

from __future__ import annotations

from helpers import git as _git

from ddflow.api.gates import _run
from ddflow.services import gates as G


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mine.py").write_text("1")
    (tmp_path / "other.py").write_text("1")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "one")
    return G.tree_identity(tmp_path)


def _commit(tmp_path, rel: str, text: str, message: str):
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", message)


def test_event_only_commit_does_not_supersede_a_gated_phase_run(tmp_path):
    # With globs now too: a gated phase's source globs do not match `.ddflow/` files, so
    # an event-only commit (ddflow's own bookkeeping) must NOT supersede its run (B4ba4673c0a).
    began = _repo(tmp_path)
    _commit(tmp_path, ".ddflow/events/shard.jsonl", "{}\n", "events: complete other")
    assert not G.superseded_by(began, G.tree_identity(tmp_path), tmp_path, paths=["mine.py"])


def test_event_only_commit_does_not_supersede_a_phase_run(tmp_path):
    # A phase has no globs, so `superseded_by` has no paths: an event-only commit
    # (`.ddflow/events`, ddflow's own bookkeeping) must NOT supersede the run.
    began = _repo(tmp_path)
    _commit(tmp_path, ".ddflow/events/shard.jsonl", "{}\n", "events: complete other")
    assert not G.superseded_by(began, G.tree_identity(tmp_path), tmp_path)


def test_a_source_commit_supersedes_without_paths(tmp_path):
    # Still superseded when a commit changed non-bookkeeping source: that IS the source
    # the phase gate ran against moving on.
    began = _repo(tmp_path)
    _commit(tmp_path, "other.py", "2", "someone else's source")
    assert G.superseded_by(began, G.tree_identity(tmp_path), tmp_path)


def test_found_text_names_the_verdict_and_summary():
    # B267a6b0b5e: a superseded run's refusal must still say what it found, so the
    # outcome is not lost. The helper carries the verdict and the suite's summary lines.
    assert _run._found_text("failed", {"summary": ["112 passed", "3 failed"]}) == (
        "FAILED 112 passed; 3 failed"
    )
    assert _run._found_text("passed", {"summary": ["10 passed"]}) == "PASSED 10 passed"
    assert _run._found_text("passed", {}) == "PASSED"


def test_found_text_names_the_failing_test_ids():
    # B4ba4673c0a: the failing test ids survive a supersede alongside the verdict.
    assert _run._found_text(
        "failed", {"summary": ["206 failed"], "failed_tests": ["tests/a.py::test_1"]}
    ) == "FAILED 206 failed; tests/a.py::test_1"
    assert _run._found_text("failed", {"failed_tests": ["tests/a.py::test_1"]}) == (
        "FAILED tests/a.py::test_1"
    )
    many = [f"tests/a.py::test_{n}" for n in range(81)]
    text = _run._found_text("failed", {"failed_tests": many})
    assert text.endswith("tests/a.py::test_79 (and 1 more)")
