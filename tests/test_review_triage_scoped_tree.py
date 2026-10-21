"""The triage pass guard compares the item's changed files, not the whole tree (bug B-triage-guard-compares-whole-tree).

B835d22b138 refused a triage pass over a tree the round did not see, comparing the WHOLE worktree: a
merge of main, an event-log commit or an edit outside the item's diff moved it, and every fix after a
round then cost another operator-granted round. The round now records `reviewed_paths` (the content of
the item's changed set) and the guard compares those paths, plus any path the item changes since.
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


def _round(repo: Path, tmp_path: Path) -> Path:
    """A claimed item in a real worktree, one failed round with two findings, nothing committed."""
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
    (wt / "pkg" / "sub").mkdir(parents=True)
    (wt / "pkg" / "sub" / "n.py").write_text("y = 1  # clean\n")
    _git(wt, "add", "-A")  # the review sends tracked changes only
    out = api.review(repo, gate="critic", item="T1", full=True)
    assert out.data["outcome"] == "failed", out.reason
    return wt


def _tri(repo: Path, n: int):
    return ST.triage(repo, "T1", gate="critic", finding=n, verdict="refuted", probe="ran it")


def _settle(repo: Path):
    _tri(repo, 1)
    return _tri(repo, 2)


def test_the_round_records_the_content_of_the_changed_set(repo, tmp_path):
    _round(repo, tmp_path)
    assert set(_state(repo).gates["critic"].evidence["reviewed_paths"]) == {
        "a.py",
        "b.py",
        "c.py",
        "pkg/sub/n.py",
    }


def test_a_main_merge_and_other_files_do_not_invalidate_the_round(repo, tmp_path):
    wt = _round(repo, tmp_path)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "work")
    (repo / "other.txt").write_text("main moved on\n")  # main gains a file the item never touched
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "main work")
    _git(wt, "merge", "-q", "--no-edit", "main")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "passed", last.data["text"]
    assert last.data["passed_on_refutation"] is True


def test_an_edit_to_a_changed_file_after_the_round_still_blocks_the_pass(repo, tmp_path):
    wt = _round(repo, tmp_path)
    (wt / "c.py").write_text("x = 2  # edited after the review\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "c.py" in last.data["text"] and "re-run" in last.data["text"]


def test_an_edit_to_a_nested_changed_file_blocks_the_pass_and_names_its_path(repo, tmp_path):
    wt = _round(repo, tmp_path)
    assert "pkg/sub/n.py" in _state(repo).gates["critic"].evidence["reviewed_paths"]
    (wt / "pkg" / "sub" / "n.py").write_text("y = 2\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "pkg/sub/n.py" in last.data["text"]


def test_a_new_file_the_item_adds_after_the_round_blocks_the_pass(repo, tmp_path):
    wt = _round(repo, tmp_path)
    (wt / "d.py").write_text("x = 3\n")
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "d.py" in last.data["text"]


def test_a_file_deleted_after_the_round_blocks_the_pass_and_is_named(repo, tmp_path):
    wt = _round(repo, tmp_path)
    (wt / "README.md").unlink()  # tracked and unchanged until now: not in the reviewed set
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "README.md" in last.data["text"]


def test_an_older_round_without_reviewed_paths_compares_the_items_whole_tree(
    repo, tmp_path, monkeypatch
):
    wt = _round(repo, tmp_path)
    monkeypatch.setattr(api.RP, "REVIEWED_PATHS_FIELD", "no_such_field")  # no snapshot recorded
    (repo / "other.txt").write_text("elsewhere\n")  # the primary's file: not the item's tree
    _tri(repo, 1)
    assert _state(repo).gates["critic"].outcome == "failed"
    (wt / "notes.md").write_text("an untracked file in the item's tree\n")
    last = _tri(repo, 2)
    assert _state(repo).gates["critic"].outcome == "failed", last.data["text"]
    assert "holds other content" in last.data["text"]


def test_an_older_round_with_the_item_tree_unchanged_still_passes(repo, tmp_path, monkeypatch):
    _round(repo, tmp_path)
    monkeypatch.setattr(api.RP, "REVIEWED_PATHS_FIELD", "no_such_field")
    (repo / "other.txt").write_text("elsewhere\n")  # the primary's file: not the item's tree
    last = _settle(repo)
    assert _state(repo).gates["critic"].outcome == "passed", last.data["text"]
