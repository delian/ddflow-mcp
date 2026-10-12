"""B-upgrade-proposal-announced-once: a pending upgrade proposal is not dropped after one brief.

The project is one a real ddflow 0.1.3 built, so the running ddflow is newer. A start outside
a container only PROPOSES; the proposal must be in every brief and in `ddflow doctor` until the
project is upgraded.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.services import upgrade_start as US

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "tests" / "fixtures" / "releases" / "0.1.3" / "project"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    return r


def test_a_proposal_is_in_every_brief_and_in_doctor_until_the_project_catches_up(old: Path) -> None:
    log, cfg, st = _load(old, "starter")
    rep = US.start(old, log, cfg, st, agent="starter", surf="mcp", environ={})
    assert rep["status"] in (US.PROPOSED, US.APPLIED) and rep["text"], rep

    first = US.take_for_brief(old, st)
    second = US.take_for_brief(old, st)
    third = US.take_for_brief(old)  # state read by the service itself
    assert first == second == third == rep["text"]
    assert US.doctor_notes(old, st) == [rep["text"]]

    code, _out, err = run_cli(
        old, "upgrade", "--apply", "--confirm", "worktree.max_parallel", "--reason", "test"
    )
    assert code == 0, err
    _log, _cfg, caught_up = _load(old, "starter")
    assert US.take_for_brief(old, caught_up) == ""
    assert US.doctor_notes(old, caught_up) == []


def test_a_hook_only_path_repeats_the_notice_and_a_read_only_store_does_not_raise(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No MCP start report exists here: the one-line notice itself must repeat."""
    run_cli(old, "config", "upgrade.on_start", "off")
    first = run_cli(old, "hooks", "session-start")[1]
    second = run_cli(old, "hooks", "session-start")[1]
    assert "Upgraded ddflow" in first and "Upgraded ddflow" in second

    def boom(*a: object, **k: object) -> None:
        raise OSError("read-only store")

    monkeypatch.setattr(US, "_remember", boom)
    US.keep_notice(old, "Upgraded ddflow x -> y")  # never raises
