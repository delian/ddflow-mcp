"""r-small-files ratchet: no new `.py` file over `[quality].max_file_bytes`, no exempt file grows.

The exemptions are the shrink-only baseline `tests/guard_baselines/file_size_baseline.txt`;
a split deletes its own line. The limit is the knob, never a constant here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.config import Config
from ddflow.config_sections.quality import QualityConfig
from ddflow.services import file_size as FS

ROOT = Path(__file__).resolve().parents[1]
LIMIT = Config().quality.max_file_bytes
#: The fixtures' limit: fixed, so a repository that sets the knob to 0 or another value still runs them.
FIXTURE_LIMIT = 30000


def test_default_limit_is_30k():
    assert QualityConfig().max_file_bytes == 30000, "the declared default, whatever an org sets"


def test_no_new_oversized_file_and_no_baselined_file_grew():
    bad = FS.violations(ROOT, LIMIT)
    assert not bad, "r-small-files:\n" + "\n".join(bad)


def test_baseline_lines_name_a_reason():
    for rel, (_, reason) in FS.read_baseline(ROOT).items():
        assert reason, f"{rel}: the baseline needs a reason"


def _repo(tmp_path: Path, size: int) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "big.py").write_text("x = 1\n" + "#" * size)
    return tmp_path


def test_an_oversized_new_file_fails_the_ratchet(tmp_path):
    repo = _repo(tmp_path, FIXTURE_LIMIT + 10)
    [msg] = FS.violations(repo, FIXTURE_LIMIT)
    assert "big.py" in msg and "split" in msg
    assert FS.violations(repo, 0) == [], "0 turns the check off"


def test_a_baselined_file_may_shrink_but_not_grow_and_a_stale_line_fails(tmp_path):
    repo = _repo(tmp_path, FIXTURE_LIMIT + 10)
    base = repo / FS.BASELINE
    base.parent.mkdir(parents=True)
    size = (repo / "big.py").stat().st_size
    base.write_text(f"big.py | {size} | legacy\n")
    assert FS.violations(repo, FIXTURE_LIMIT) == []
    base.write_text(f"big.py | {size - 1} | legacy\n")
    assert "grew" in FS.violations(repo, FIXTURE_LIMIT)[0]
    (repo / "big.py").write_text("x = 1\n")
    assert "delete its line" in FS.violations(repo, FIXTURE_LIMIT)[0]


def test_doctor_note_lists_the_files_over_the_limit(tmp_path):
    repo = _repo(tmp_path, FIXTURE_LIMIT + 10)
    [note] = FS.doctor_notes(repo, FIXTURE_LIMIT)
    assert "big.py" in note and "max_file_bytes" in note
    assert FS.doctor_notes(repo, FIXTURE_LIMIT * 2) == []


def test_limit_zero_turns_the_ratchet_off_even_with_a_baseline(tmp_path):
    repo = _repo(tmp_path, FIXTURE_LIMIT + 10)
    base = repo / FS.BASELINE
    base.parent.mkdir(parents=True)
    base.write_text("gone.py | 99999 | legacy\n")
    assert FS.violations(repo, 0) == []


def _outside_any_repo(tmp_path: Path, monkeypatch) -> Path:
    """A directory git cannot treat as part of a repository, wherever pytest put `tmp_path`
    (`--basetemp` or TMPDIR may sit inside a work tree): git stops looking upward at it."""
    work = tmp_path / "plain"
    work.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return work


def test_a_listing_git_cannot_give_is_an_error_not_an_empty_result(tmp_path, monkeypatch):
    import pytest

    tmp_path = _outside_any_repo(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="cannot measure"):
        FS.violations(tmp_path, FIXTURE_LIMIT)  # not a git repository


def test_the_doctor_note_is_silent_when_git_cannot_answer(tmp_path, monkeypatch):
    tmp_path = _outside_any_repo(tmp_path, monkeypatch)
    (tmp_path / "huge.py").write_text("#" * (FIXTURE_LIMIT + 10))
    assert FS.doctor_notes(tmp_path, FIXTURE_LIMIT) == []
