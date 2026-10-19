"""Be305b7d6ea: a fix task attaches only globs that exist in the tree."""

from __future__ import annotations

from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog
from ddflow.services.bug_globs import existing_globs


def test_existing_globs_keeps_paths_and_patterns_that_match(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_text("")
    kept, dropped = existing_globs(tmp_path, ["a/x.py", "a/*.py", "a", "a/nope.py", "b/**"])
    assert kept == ["a/x.py", "a/*.py", "a"] and dropped == ["a/nope.py", "b/**"]


def test_a_guessed_glob_that_matches_nothing_is_not_attached(repo):
    run_cli(repo, "init")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "real.py").write_text("")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    out = K.bug_found(
        repo, summary="s", id="Bx", agent="a", globs="src/real.py,src/guessed/standards.py"
    )
    assert out.exit == 0, out.reason
    fix = state(repo).items["fix-Bx"]
    assert fix.globs == ["src/real.py"]
    assert "src/guessed/standards.py" in fix.body
