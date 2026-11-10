"""Be305b7d6ea: a fix task attaches only globs that exist in the tree."""

from __future__ import annotations

import subprocess

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


def test_an_anchored_glob_is_checked_against_the_tree_not_the_host(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "p.py").write_text("")
    kept, dropped = existing_globs(tmp_path, ["/src/p.py", "/src/*.py", "/etc/hosts"])
    assert kept == ["/src/p.py", "/src/*.py"] and dropped == ["/etc/hosts"]


def _seed(repo):
    run_cli(repo, "init")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "real.py").write_text("")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append(
        "task.added",
        "T1",
        {"parent": "P1", "title": "t", "globs": ["src/real.py", "src/guessed/standards.py"]},
    )


def test_an_inherited_glob_that_matches_nothing_is_not_attached(repo):
    _seed(repo)
    out = K.bug_found(repo, summary="s", id="Bx", agent="a", item="T1")
    assert out.exit == 0, out.reason
    fix = state(repo).items["fix-Bx"]
    assert fix.globs == ["src/real.py"]
    assert "src/guessed/standards.py" in fix.body


def test_an_explicit_glob_is_kept_even_when_it_names_a_file_the_fix_creates(repo):
    _seed(repo)
    out = K.bug_found(repo, summary="s", id="By", agent="a", globs="src/new_module.py")
    assert out.exit == 0, out.reason
    assert state(repo).items["fix-By"].globs == ["src/new_module.py"]


def test_an_explicit_glob_naming_no_file_is_loudly_flagged(repo):
    """Bf839186cee: `--globs` naming a file that names no tracked file held a lease on a
    path nobody writes while the real file stayed unprotected, and nothing said so at
    filing. A TRACKED file deleted from the worktree is still its path (in scope, no
    warning); a path that names no tracked file is a wrong scope (warned)."""
    _seed(repo)
    subprocess.run(["git", "-C", str(repo), "add", "src/real.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "track real"], check=True)
    (repo / "src" / "real.py").unlink()  # tracked but gone from the worktree: still the scope
    out = K.bug_found(
        repo,
        summary="s",
        id="Bz",
        agent="a",
        globs="src/real.py,src/guessed/standards.py",
    )
    assert out.exit == 0, out.reason
    warned = " ".join(out.data.get("glob_warnings", []))
    assert "src/guessed/standards.py" in warned
    assert "src/real.py" not in warned
