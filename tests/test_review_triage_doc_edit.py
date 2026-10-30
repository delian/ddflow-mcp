"""A documentation edit after a review round does not void its triage pass (bug Bb89ff23f21).

A review judged a doc and the code side by side, chunked apart, and the reviewer called the
doc's claim "unenforced" because the check was in the sibling chunk. Refuting that finding
settled the round, but a one-line doc edit after the triage moved the reviewed doc, so the
settled round "held other content now" and cost a second 8-15 min round; a wording-only edit
after the final pass still warned "different tree" at complete. Documentation
(`[review].doc_globs`) is wording, not code: its change is never new work, so it does not
void a pass -- a reviewed CODE file that changed still blocks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_triage import _setup, _state

import ddflow.api.review as api
import ddflow.api.review_settle as ST


def _git(wt: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True)


def _round_doc(repo: Path, tmp_path: Path) -> Path:
    """A claimed item in a real worktree, one failed round over code AND a doc, nothing committed."""
    _setup(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("enabled = false", "enabled = true"))
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).unlink()
    _git(repo, "add", "-A")
    run_cli(repo, "claim", "T1")
    wt = (repo / _state(repo).worktree).resolve()
    for name, mark in (("a.py", "FINDA"), ("b.py", "FINDB"), ("c.py", "clean")):
        (wt / name).write_text(f"x = 1  # {mark} " + "p" * 60 + "\n")
    (wt / "docs").mkdir()
    (wt / "docs" / "notes.md").write_text("# notes\n\nthe inventory table is checked by a test\n")
    _git(wt, "add", "-A")  # the review sends tracked changes only
    out = api.review(repo, gate="critic", item="T1", full=True)
    assert out.data["outcome"] == "failed", out.reason
    return wt


def _tri(repo: Path, n: int):
    return ST.triage(repo, "T1", gate="critic", finding=n, verdict="refuted", probe="ran it")


def _settle(repo: Path):
    _tri(repo, 1)
    return _tri(repo, 2)


def test_the_doc_was_reviewed_and_then_edited(repo, tmp_path):
    wt = _round_doc(repo, tmp_path)
    assert "docs/notes.md" in _state(repo).gates["critic"].evidence["reviewed_paths"]
    (wt / "docs" / "notes.md").write_text(
        "# notes\n\nwording clarified: the table check lives in the sibling test\n"
    )
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "passed", last.data["text"]


def test_a_reviewed_doc_deleted_after_the_round_still_blocks_and_is_named(repo, tmp_path):
    wt = _round_doc(repo, tmp_path)
    (wt / "docs" / "notes.md").unlink()  # reviewed, then deleted: not wording, still new work
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "docs/notes.md" in last.data["text"]


def test_a_reviewed_code_file_that_changed_still_blocks_the_pass(repo, tmp_path):
    wt = _round_doc(repo, tmp_path)
    (wt / "c.py").write_text("x = 2  # edited after the review\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "c.py" in last.data["text"]
