"""`ci run` names why its scratch merge conflicted (DX-ci-merge-conflict-cause): the files,
which of them are pin files, and the remedy."""

from __future__ import annotations

from helpers import commit as _commit
from helpers import git as _git
from test_ci_scratch_merge_hooks import _diverged, passing_precommit  # noqa: F401 -- fixture

from ddflow.config import Config
from ddflow.services import ci as CI


def _conflict(repo, files: dict[str, str], pins: str = ""):
    default = _diverged(repo)
    if pins:
        (repo / ".ddflow" / "pins.toml").write_text(pins)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "pins")
    _git(repo, "switch", "-q", default)
    _commit(repo, dict.fromkeys(files, "main\n"), "main edits")
    _git(repo, "switch", "-q", "work")
    _commit(repo, dict.fromkeys(files, "work\n"), "work edits")
    return default


PIN = '[[pin]]\nname = "readme"\nfiles = ["README.md"]\ncheck = ["true"]\nregen = ["true"]\n'


def test_a_pin_file_conflict_names_the_file_and_the_remedy(repo, passing_precommit):  # noqa: F811
    """Fails on the unfixed code: the reason held only git's own lines."""
    default = _conflict(repo, {"README.md": ""}, pins=PIN)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed", res
    assert "conflicting files: README.md" in res.reason, res.reason
    assert "is a pin file" in res.reason and "ddflow pins regenerate" in res.reason


def test_an_ordinary_conflict_names_its_files_and_no_pin_remedy(repo, passing_precommit):  # noqa: F811
    default = _conflict(repo, {"notes.txt": ""}, pins=PIN)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed", res
    assert "conflicting files: notes.txt" in res.reason
    assert "pin file" not in res.reason and "resolve notes.txt" in res.reason


def test_a_mixed_conflict_separates_pin_files_from_the_rest(repo, passing_precommit):  # noqa: F811
    default = _conflict(repo, {"README.md": "", "notes.txt": ""}, pins=PIN)
    res = CI.run(repo, Config.load(repo), base=default)
    assert "README.md is a pin file" in res.reason and "resolve notes.txt" in res.reason
