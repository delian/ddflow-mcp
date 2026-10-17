"""Bug B1579b8a1a9: the brief says pin files are regenerated, never leased, but committing a
regenerated pin needed a lease and another item usually held one. What `ddflow pins
regenerate` wrote commits without a lease, and only as written."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pins_regenerate import ddflow_cli, git, repo  # noqa: F401 -- the pins fixture

from ddflow.config import Config
from ddflow.core.outcome import FAIL, OK
from ddflow.services import enforce as E


def _verdict(repo: Path):
    cfg = Config.load(repo)
    cfg.enforce.commit_without_lease = "block"
    return E.check_commit(repo, cfg, agent="regenerator")


def _stale(repo: Path) -> None:
    (repo / "knobs.txt").write_text("a\nb\n")
    assert ddflow_cli(repo, "pins", "regenerate").returncode == 0


def test_a_regenerated_pin_commits_without_a_lease(repo: Path) -> None:  # noqa: F811
    _stale(repo)
    git(repo, "add", "pin.txt")
    verdict = _verdict(repo)
    assert verdict.exit == OK, verdict.message


def test_a_later_edit_of_the_pin_still_needs_a_lease(repo: Path) -> None:  # noqa: F811
    _stale(repo)
    with (repo / "pin.txt").open("a") as f:
        f.write("edited by hand\n")
    git(repo, "add", "pin.txt")
    verdict = _verdict(repo)
    assert verdict.exit == FAIL and "pin.txt" in verdict.message


def test_a_file_the_recipes_did_not_write_still_needs_a_lease(repo: Path) -> None:  # noqa: F811
    _stale(repo)
    (repo / "other.txt").write_text("changed by someone\n")
    git(repo, "add", "other.txt")
    verdict = _verdict(repo)
    assert verdict.exit == FAIL and "other.txt" in verdict.message


@pytest.mark.parametrize("mode", ["--check"])
def test_a_check_records_nothing(repo: Path, mode: str) -> None:  # noqa: F811
    (repo / "knobs.txt").write_text("a\nb\n")
    assert ddflow_cli(repo, "pins", "regenerate", mode).returncode == 1
    (repo / "pin.txt").write_text("1 knobs\n- a\n- b\n")  # by hand, not by the tool
    git(repo, "add", "pin.txt")
    assert _verdict(repo).exit == FAIL
