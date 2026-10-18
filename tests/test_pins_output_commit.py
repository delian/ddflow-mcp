"""Bug B1579b8a1a9: the brief says pin files are regenerated, never leased, but committing a
regenerated pin needed a lease and another item usually held one. What `ddflow pins
regenerate` wrote commits without a lease, and only as written."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pins_regenerate import ddflow_cli, git
from test_pins_regenerate import repo as pins_repo

from ddflow.config import Config
from ddflow.core.outcome import FAIL, OK
from ddflow.services import enforce as E

repo = pins_repo  # the pins fixture, under the name the tests ask for


def _verdict(repo: Path):
    cfg = Config.load(repo)
    cfg.enforce.commit_without_lease = "block"
    return E.check_commit(repo, cfg, agent="regenerator")


def _stale(repo: Path) -> None:
    (repo / "knobs.txt").write_text("a\nb\n")
    assert ddflow_cli(repo, "pins", "regenerate").returncode == 0


def test_a_regenerated_pin_commits_without_a_lease(repo: Path) -> None:
    _stale(repo)
    git(repo, "add", "pin.txt")
    verdict = _verdict(repo)
    assert verdict.exit == OK, verdict.message


def test_a_later_edit_of_the_pin_still_needs_a_lease(repo: Path) -> None:
    _stale(repo)
    with (repo / "pin.txt").open("a") as f:
        f.write("edited by hand\n")
    git(repo, "add", "pin.txt")
    verdict = _verdict(repo)
    assert verdict.exit == FAIL and "pin.txt" in verdict.message


def test_a_file_the_recipes_did_not_write_still_needs_a_lease(repo: Path) -> None:
    _stale(repo)
    (repo / "other.txt").write_text("changed by someone\n")
    git(repo, "add", "other.txt")
    verdict = _verdict(repo)
    assert verdict.exit == FAIL and "other.txt" in verdict.message


@pytest.mark.parametrize("mode", ["--check"])
def test_a_check_records_nothing(repo: Path, mode: str) -> None:
    (repo / "knobs.txt").write_text("a\nb\n")
    assert ddflow_cli(repo, "pins", "regenerate", mode).returncode == 1
    (repo / "pin.txt").write_text("1 knobs\n- a\n- b\n")  # by hand, not by the tool
    git(repo, "add", "pin.txt")
    assert _verdict(repo).exit == FAIL


def test_a_damaged_record_degrades_to_no_exemption_not_a_crash(repo: Path) -> None:
    record = repo / ".ddflow" / "local" / "pins-written.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"pin.txt": "cafe", "other.txt": null, "x": [1, "ok"]}')
    _stale(repo)  # the regeneration must still record its output
    git(repo, "add", "pin.txt")
    assert _verdict(repo).exit == OK


def test_a_pin_regenerated_in_a_linked_worktree_commits_there(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """The commit hook runs with the PRIMARY checkout as ``repo`` while the commit is made in
    the worktree; the record written by a regeneration there must be the one the hook reads."""
    tree = tmp_path / "tree"
    git(repo, "worktree", "add", "-q", "-b", "side", str(tree))
    (tree / "knobs.txt").write_text("a\nb\n")
    done = ddflow_cli(tree, "pins", "regenerate")
    assert done.returncode == 0, done.stderr
    git(tree, "add", "pin.txt")
    monkeypatch.chdir(tree)
    assert _verdict(repo).exit == OK


def test_two_recorders_do_not_lose_each_others_digests(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from ddflow.services import pins_written as PW

    def run(tag: str) -> None:
        for n in range(40):
            PW.record(tmp_path, {f"{tag}{n}.txt": f"d{n}"})

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(run, "abcd"))
    assert len(PW.load(tmp_path)) == 4 * 40


def test_the_post_merge_regeneration_records_its_output_too(repo: Path) -> None:
    """roborev on this fix: the `--after-merge` path (`[pins].after_merge = "leave"`) re-records
    pins and says "commit them", which then needed a lease."""
    (repo / "knobs.txt").write_text("a\nb\n")
    done = ddflow_cli(repo, "pins", "regenerate", "--after-merge")
    assert done.returncode == 0, done.stderr
    assert "2 knobs" in (repo / "pin.txt").read_text()
    git(repo, "add", "pin.txt")
    assert _verdict(repo).exit == OK


def test_the_attributes_line_a_regeneration_adds_commits_without_a_lease(repo: Path) -> None:
    """DX-pins-gitattributes-dirty: `pins regenerate` adds a recipe's `merge=pinsregen` line to
    `.gitattributes`; committing that needed a lease, and merge refused the dirty tree."""
    (repo / ".gitattributes").write_text("README.md merge=pinsregen\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "attrs")
    done = ddflow_cli(repo, "pins", "regenerate")
    assert done.returncode == 0, done.stderr
    assert "pin.txt merge=pinsregen" in (repo / ".gitattributes").read_text()
    git(repo, "add", ".gitattributes")
    verdict = _verdict(repo)
    assert verdict.exit == OK, verdict.message


def test_a_later_edit_of_the_attributes_still_needs_a_lease(repo: Path) -> None:
    (repo / ".gitattributes").write_text("README.md merge=pinsregen\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "attrs")
    assert ddflow_cli(repo, "pins", "regenerate").returncode == 0
    with (repo / ".gitattributes").open("a") as f:
        f.write("*.json -diff\n")
    git(repo, "add", ".gitattributes")
    verdict = _verdict(repo)
    assert verdict.exit == FAIL and ".gitattributes" in verdict.message
