"""Conflict markers a merge left in a pin file (DX-pins-resolve-readme-conflict)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import pins as PN
from ddflow.services import pins_conflict as PC


@pytest.fixture(autouse=True)
def _outside_xdist(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def _block(word: str) -> str:
    return f"<!-- ddflow:begin t -->\nvalue {word}\n<!-- ddflow:end t -->\n"


def _conflict(ours: str, theirs: str) -> str:
    return f"<<<<<<< HEAD\n{ours}=======\n{theirs}>>>>>>> main\n"


#: `regen` writes the block the way the generator would; `check` is green once it holds.
RECIPE = (
    '[[pin]]\nname = "t"\nfiles = ["README.md"]\n'
    f'check = ["{sys.executable}", "-c", "import sys; t = open(\'README.md\').read(); '
    f"sys.exit(0 if 'value good' in t and '<<<<' not in t else 1)\"]\n"
    f'regen = ["{sys.executable}", "-c", "import re; t = open(\'README.md\').read(); '
    "open('README.md', 'w').write(re.sub(r'value \\\\w+', 'value good', t))\"]\n"
)


def _repo(tmp_path: Path, readme: str) -> Path:
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "pins.toml").write_text(RECIPE)
    (tmp_path / "README.md").write_text(readme)
    return tmp_path


def test_a_conflict_inside_a_generated_region_is_settled_and_regenerated(tmp_path):
    repo = _repo(tmp_path, "intro\n" + _conflict(_block("a"), _block("b")) + "outro\n")
    rep = PN.regenerate(repo, Config())
    assert not rep.problem and not rep.failed
    text = (repo / "README.md").read_text()
    assert "<<<<" not in text and ">>>>" not in text and "=======" not in text
    assert text == "intro\n" + _block("good") + "outro\n"
    assert rep.resolved and "README.md" in rep.resolved[0]


def test_a_conflict_that_opens_inside_the_region_is_settled(tmp_path):
    inner = (
        "<!-- ddflow:begin t -->\n"
        + _conflict("value a\n", "value b\n")
        + "<!-- ddflow:end t -->\n"
    )
    repo = _repo(tmp_path, "intro\n" + inner + "outro\n")
    rep = PN.regenerate(repo, Config())
    assert not rep.problem and not rep.failed
    assert (repo / "README.md").read_text() == "intro\n" + _block("good") + "outro\n"


def test_a_prose_conflict_is_refused_and_nothing_is_changed(tmp_path):
    readme = (
        "intro\n"
        + _conflict("mine\n", "theirs\n")
        + _block("a")
        + _conflict(_block("a"), _block("b"))
    )
    repo = _repo(tmp_path, readme)
    rep = PN.regenerate(repo, Config())
    assert "prose" in rep.problem and "README.md" in rep.problem and "line 2" in rep.problem
    assert (repo / "README.md").read_text() == readme
    assert rep.pins == []  # no recipe ran


def test_check_does_not_touch_a_conflicted_file(tmp_path):
    readme = "intro\n" + _conflict(_block("a"), _block("b"))
    repo = _repo(tmp_path, readme)
    rep = PN.regenerate(repo, Config(), check=True)
    assert [p.name for p in rep.stale] == ["t"]
    assert (repo / "README.md").read_text() == readme


def test_a_setext_underline_is_not_a_conflict(tmp_path):
    assert PC.hunks_of(["Title\n", "=======\n", "text\n"]) == []
    assert PC.hunks_of(["<<<<<<< HEAD\n", "x\n"]) == []  # never closed


def test_a_file_with_no_region_is_generated_whole(tmp_path):
    res = PC.settle("t.json", "{\n" + _conflict('  "a": 1\n', '  "a": 2\n') + "}\n")
    assert res.refused == [] and res.text == '{\n  "a": 1\n}\n'


def test_diff3_base_section_belongs_to_neither_side():
    lines = "<<<<<<< HEAD\nours\n||||||| base\nold\n=======\ntheirs\n>>>>>>> main\n".splitlines(
        True
    )
    (hunk,) = PC.hunks_of(lines)
    assert hunk[2].ours == ["ours\n"] and hunk[2].theirs == ["theirs\n"]


def test_a_real_merge_conflict_in_the_generated_block_is_cleared(tmp_path):
    import subprocess

    def git(*a):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *a],
            capture_output=True,
            text=True,
        )

    repo = _repo(tmp_path, "intro\n" + _block("base") + "outro\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "work")
    (repo / "README.md").write_text("intro\n" + _block("mine") + "outro\n")
    git("commit", "-qam", "work")
    git("checkout", "-q", "main")
    (repo / "README.md").write_text("intro\n" + _block("main") + "outro\n")
    git("commit", "-qam", "main")
    git("checkout", "-q", "work")
    assert git("merge", "main", "-m", "m").returncode != 0
    assert "<<<<<<<" in (repo / "README.md").read_text()
    rep = PN.regenerate(repo, Config())
    assert not rep.problem and not rep.failed and rep.resolved
    assert (repo / "README.md").read_text() == "intro\n" + _block("good") + "outro\n"
    git("add", "README.md")
    assert git("commit", "-qm", "merge").returncode == 0


def test_nested_or_unclosed_markers_leave_the_file_to_a_person(tmp_path):
    nested = "<<<<<<< HEAD\nours\n" + _conflict("x\n", "y\n") + "=======\ntheirs\n>>>>>>> main\n"
    res = PC.settle("t.json", nested)
    assert res.text is None and res.refused == [1, 10]
    repo = _repo(tmp_path, "intro\n" + _conflict(_block("a"), _block("b")) + "<<<<<<< HEAD\nx\n")
    before = (repo / "README.md").read_text()
    assert "line 6" in PN.regenerate(repo, Config()).problem
    assert (repo / "README.md").read_text() == before
