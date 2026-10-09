"""B-upgrade.6-notice: the session-start upgrade notice and the `[upgrade].auto` policy.

Decision D-upgrade-auto-check: after ddflow is upgraded, the first session start on a machine
says so in ONE line -- once per machine per version -- and with the shipped default writes
nothing. `safe` also applies the non-destructive categories (hooks, instructions) after a
backup; `off` says nothing. The project here is one a real ddflow 0.1.3 built
(tests/fixtures/releases/0.1.3), so the running ddflow is genuinely newer than the project.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.core.model import State
from ddflow.services import upgrade_notice as UN

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
LINE = "ddflow upgraded"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """The 0.1.3-built project, as a git repository with one commit."""
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    return r


def _start(repo: Path) -> str:
    code, out, err = run_cli(repo, "hooks", "session-start")
    assert code == 0, err
    return out


def _totals(repo: Path) -> dict[str, int]:
    _code, out, _err = run_cli(repo, "--json", "upgrade")
    return {k: len(v) for k, v in json.loads(out)["categories"].items()}


def test_the_default_is_check() -> None:
    from ddflow.config import Config

    assert Config().upgrade.auto == "check"


def test_the_notice_appears_once_not_twice(old: Path) -> None:
    first = _start(old)
    assert LINE in first and "ddflow upgrade --plan" in first
    assert first.count(LINE) == 1
    assert LINE not in _start(old), "said once per machine per version"
    marker = json.loads((old / ".ddflow" / "local" / "upgrade-notice.json").read_text())
    assert marker["version"] and LINE in marker["said"]


def test_check_writes_nothing(old: Path) -> None:
    before = _totals(old)
    status = subprocess.run(
        ["git", "-C", str(old), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert LINE in _start(old)
    assert _totals(old) == before, "check only says it: no category of the plan moved"
    after = subprocess.run(
        ["git", "-C", str(old), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert after == status


def test_off_prints_nothing_and_leaves_no_marker(old: Path) -> None:
    run_cli(old, "config", "upgrade.auto", "off")
    assert LINE not in _start(old)
    assert not (old / ".ddflow" / "local" / "upgrade-notice.json").exists()


def test_safe_applies_hooks_and_instructions_only_after_a_backup(old: Path) -> None:
    run_cli(old, "config", "upgrade.auto", "safe")
    before = _totals(old)
    assert before["hooks"] and before["instructions"] and before["config"]
    out = _start(old)
    assert LINE in out and "applied hooks and instructions" in out and "backup" in out
    after = _totals(old)
    assert after["hooks"] == 0 and after["instructions"] == 0
    assert after["config"] == before["config"], "config defaults are the operator's"
    assert any((old / ".ddflow" / "backups").iterdir()), "the originals were saved first"
    assert LINE not in _start(old)


def test_a_project_that_is_up_to_date_hears_nothing(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    assert LINE not in _start(repo)


def test_a_new_version_is_said_again(old: Path) -> None:
    _start(old)
    marker = old / ".ddflow" / "local" / "upgrade-notice.json"
    marker.write_text(json.dumps({"version": "0.0.1", "said": "x"}))
    assert LINE in _start(old), "the marker names an older version: this one is news"


def test_an_unwritable_marker_costs_only_a_repeat(old: Path) -> None:
    (old / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
    (
        old / ".ddflow" / "local" / "upgrade-notice.json"
    ).mkdir()  # a directory: not writable as a file
    assert LINE in _start(old)  # never raises, never fails the hook


# --- the stale MCP server ---------------------------------------------------------------------


def _state(highest: str = "") -> State:
    st = State()
    st.ddflow_versions = {highest: {"agents": [], "at": "", "install": ""}} if highest else {}
    return st


def test_a_server_older_than_the_installed_package_says_restart() -> None:
    note = UN.stale_server_note(_state(), running="0.1.9", installed="0.1.10")
    assert "restart the server" in note and "0.1.9" in note and "0.1.10" in note
    assert "installed package" in note


def test_a_server_older_than_the_log_says_so() -> None:
    note = UN.stale_server_note(_state("0.1.12"), running="0.1.9", installed="0.1.9")
    assert "restart the server" in note and "this project's log" in note


def test_the_newest_version_wins_the_note() -> None:
    note = UN.stale_server_note(_state("0.1.12"), running="0.1.9", installed="0.1.10")
    assert "0.1.12" in note and "0.1.10" not in note


@pytest.mark.parametrize(
    ("running", "installed", "highest"),
    [
        ("0.1.10", "0.1.10", ""),
        ("0.1.10", "0.1.9", "0.1.9"),
        ("", "0.1.10", ""),
        ("0.1.10", "", ""),
    ],
)
def test_a_current_server_says_nothing(running: str, installed: str, highest: str) -> None:
    assert UN.stale_server_note(_state(highest), running=running, installed=installed) == ""
