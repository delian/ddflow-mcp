"""[review].exclude_globs: generated and pinned files are not sent to reviewers, and the
output and evidence say how many files and bytes were left out (DX-review-exclude)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import review_exclude as REX


def _sec(path: str, body: str = "x") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+{body}\n"


def test_the_defaults_name_golden_transcripts_lockfiles_and_snapshots():
    cfg = Config.load(env={})
    assert cfg.review.exclude_globs == [
        "tests/golden/**", "*transcript*.json", "uv.lock", "*.ambr",
    ]  # fmt: skip
    assert cfg.review.exclude_pin_files is True


def test_filter_drops_matching_files_and_counts_them():
    diff = (
        _sec("a.py")
        + _sec("tests/golden/x.json")
        + _sec("sub/loop_transcript.json")
        + _sec("uv.lock")
    )
    kept, gone = REX.filter_diff(diff, ["tests/golden/**", "*transcript*.json", "uv.lock"])
    assert "a.py" in kept and "golden" not in kept and "uv.lock" not in kept
    assert [p for p, _ in gone.files] == [
        "tests/golden/x.json", "sub/loop_transcript.json", "uv.lock",
    ]  # fmt: skip
    assert gone.bytes > 0 and "3 file(s)" in gone.line()


def test_nothing_matching_returns_the_diff_untouched():
    diff = _sec("a.py")
    assert REX.filter_diff(diff, ["*.ambr"]) == (diff, REX.Excluded())


def _setup(repo: Path, tmp_path: Path, review_toml: str = "") -> Path:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        f"#!/bin/sh\nin=$(cat)\nprintf '%s\\n' \"$in\" >> '{seen}'\necho 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n' + review_toml
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*")
    (repo / "x.py").write_text("x = 1\n")
    (repo / "tests" / "golden").mkdir(parents=True)
    (repo / "tests" / "golden" / "snap.json").write_text('{"GENERATED_MARK": 1}\n')
    return seen


def test_a_review_leaves_out_excluded_files_and_says_so(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 0, out.reason
    sent = seen.read_text()
    assert "x.py" in sent and "GENERATED_MARK" not in sent
    assert (
        "excluded from review" in out.data["text"] and "tests/golden/snap.json" in out.data["text"]
    )
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert ev["excluded_files"] == 1 and ev["excluded_bytes"] > 0
    assert ev["excluded_paths"] == ["tests/golden/snap.json"]


def test_an_empty_list_sends_every_file(repo, tmp_path):
    seen = _setup(repo, tmp_path, "[review]\nexclude_globs = []\n")
    api.review(repo, gate="critic", item="T1")
    assert "GENERATED_MARK" in seen.read_text()


def test_pin_files_are_left_out_unless_the_knob_says_otherwise(repo, tmp_path):
    seen = _setup(repo, tmp_path, "[review]\nexclude_globs = []\n")
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "p"\nfiles = ["tests/golden/snap.json"]\ncheck = ["true"]\nregen = ["true"]\n'
    )
    api.review(repo, gate="critic", item="T1")
    assert "GENERATED_MARK" not in seen.read_text()


def test_only_excluded_files_changed_records_unavailable_naming_them(repo, tmp_path):
    _setup(repo, tmp_path)
    (repo / "x.py").unlink()
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 2 and "excluded file(s)" in out.reason


def test_a_rename_out_of_an_excluded_tree_keeps_its_new_side():
    diff = "diff --git a/tests/golden/x.json b/src/x.json\nsimilarity index 90%\nrename from tests/golden/x.json\nrename to src/x.json\n"
    kept, gone = REX.filter_diff(diff, ["tests/golden/**"])
    assert kept == diff and not gone.files


def test_a_pin_file_is_an_exact_path_not_an_any_depth_pattern():
    diff = _sec("README.md") + _sec("docs/README.md") + _sec("snaps/a.ambr")
    kept, gone = REX.filter_diff(diff, [], ["README.md", "snaps/"])
    assert [p for p, _ in gone.files] == ["README.md", "snaps/a.ambr"]
    assert "docs/README.md" in kept
