"""r-small-files ratchet: no new `.py` file over `[quality].max_file_bytes`, no exempt file grows.

The exemptions are the shrink-only baseline `tests/file_size_baseline.txt`;
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
    msg = FS.violations(repo, FIXTURE_LIMIT)[0]
    assert "delete its line" in msg
    assert f"big.py | {size - 1} | legacy" in msg, "the message quotes the exact line to delete"


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


# -- growth against a base: the check a merge can run on its own diff (GR-size-ratchet) ------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def _based(tmp_path: Path, files: dict[str, int]) -> Path:
    """A repo whose first commit holds each file at the given size; HEAD~0 is the base."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, size in files.items():
        (tmp_path / name).write_text("x = 1\n" + "#" * size)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_growth_rejects_a_file_that_crosses_the_limit(tmp_path):
    repo = _based(tmp_path, {"ok.py": FIXTURE_LIMIT - 100})
    (repo / "ok.py").write_text("x = 1\n" + "#" * (FIXTURE_LIMIT + 50))
    [msg] = FS.growth(repo, ["ok.py"], FIXTURE_LIMIT, "HEAD")
    assert "ok.py" in msg and "over the" in msg and "split" in msg


def test_growth_rejects_a_new_oversized_file(tmp_path):
    repo = _based(tmp_path, {"a.py": 10})
    (repo / "big.py").write_text("#" * (FIXTURE_LIMIT + 1))
    [msg] = FS.growth(repo, ["big.py"], FIXTURE_LIMIT, "HEAD")
    assert "big.py" in msg and "split it into a package" in msg


def test_growth_rejects_a_file_already_over_the_limit_that_grew_baselined_or_not(tmp_path):
    repo = _based(tmp_path, {"old.py": FIXTURE_LIMIT + 500})
    size = (repo / "old.py").stat().st_size
    (repo / "old.py").write_text("x = 1\n" + "#" * (FIXTURE_LIMIT + 600))
    [msg] = FS.growth(repo, ["old.py"], FIXTURE_LIMIT, "HEAD")
    assert "grew" in msg and str(size) in msg
    base = repo / FS.BASELINE  # a baseline line does not license growth either
    base.parent.mkdir(parents=True)
    now = (repo / "old.py").stat().st_size
    base.write_text(f"old.py | {now} | legacy\n")  # a baseline that WOULD mask the growth
    before = base.read_text()
    assert "grew" in FS.growth(repo, ["old.py"], FIXTURE_LIMIT, "HEAD")[0]
    assert base.read_text() == before, "a rejection never edits the baseline"


def test_growth_allows_a_shrink_an_untouched_offender_and_a_small_change(tmp_path):
    repo = _based(tmp_path, {"old.py": FIXTURE_LIMIT + 500, "small.py": 10})
    (repo / "old.py").write_text("x = 1\n" + "#" * (FIXTURE_LIMIT + 100))  # shrank, still over
    (repo / "small.py").write_text("x = 2\n")
    assert (
        FS.growth(repo, ["old.py", "small.py", "gone.py", "notes.md"], FIXTURE_LIMIT, "HEAD") == []
    )


def test_growth_is_off_at_limit_zero_and_never_edits_a_limit_or_baseline(tmp_path):
    repo = _based(tmp_path, {"old.py": FIXTURE_LIMIT + 500})
    (repo / "old.py").write_text("x = 1\n" + "#" * (FIXTURE_LIMIT + 900))
    assert FS.growth(repo, ["old.py"], 0, "HEAD") == []
    assert not (repo / FS.BASELINE).exists()


def test_growth_with_an_unreadable_base_is_an_error_not_a_pass(tmp_path):
    import pytest

    repo = _based(tmp_path, {"a.py": 10})
    (repo / "a.py").write_text("#" * (FIXTURE_LIMIT + 1))
    with pytest.raises(RuntimeError, match="cannot measure"):
        FS.growth(repo, ["a.py"], FIXTURE_LIMIT, "no-such-ref")


def test_a_touched_file_that_cannot_be_statted_is_an_error_not_a_pass(tmp_path):
    import pytest

    repo = _based(tmp_path, {"a.py": 10})
    (repo / "loop.py").symlink_to("loop.py")  # ELOOP on stat, not a deletion
    with pytest.raises(RuntimeError, match=r"cannot measure loop\.py"):
        FS.growth(repo, ["loop.py"], FIXTURE_LIMIT, "HEAD")


def test_a_dangling_link_is_not_read_as_a_deletion(tmp_path):
    import pytest

    repo = _based(tmp_path, {"a.py": 10})
    (repo / "foo.py").symlink_to("missing.py")
    with pytest.raises(RuntimeError, match="dangling"):
        FS.growth(repo, ["foo.py"], FIXTURE_LIMIT, "HEAD")
