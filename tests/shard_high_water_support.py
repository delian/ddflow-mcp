"""Shared fixtures and helpers of the shard high-water tests (split from test_shard_high_water.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from tests.conftest import run_cli

AGENT = "U1"


def _dd(repo: Path, *argv: str, agent: str = AGENT) -> tuple[int, str, str]:
    return run_cli(repo, *argv, agent=agent)


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _shard(repo: Path, name: str = f"{AGENT}.jsonl") -> Path:
    return repo / ".ddflow" / "events" / name


def _lines(repo: Path, name: str = f"{AGENT}.jsonl") -> int:
    return len([ln for ln in _shard(repo, name).read_text().splitlines() if ln.strip()])


@pytest.fixture(name="clone")
def _clone_fixture(tmp_path: Path) -> Path:
    """A clone with normal use behind it: a task, a claim, a heartbeat, a second task, each
    commit adding events, so older commits hold fewer of them."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(tmp_path / "a")], check=True)
    a = tmp_path / "a"
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(a, "config", k, v)
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert _dd(a, "task", "add", "T1", "--title", "t", "--globs", "src/t.py")[0] == 0
    _commit(a, "T1")
    assert _dd(a, "claim", "T1")[0] == 0
    assert _dd(a, "heartbeat", "T1")[0] == 0
    _commit(a, "claim T1")
    assert _dd(a, "task", "add", "T2", "--title", "u", "--globs", "src/u.py")[0] == 0
    _commit(a, "T2")
    _git(a, "push", "-q", "origin", "main")
    return a


def _doctor(repo: Path) -> tuple[int, str]:
    code, out, err = _dd(repo, "doctor")
    return code, out + err


def _problem_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "PROBLEM" in ln and "lost" in ln]


def _marks_path(repo: Path) -> Path:
    return repo / ".ddflow" / "local" / "shard-marks"


def _marks_doc(repo: Path) -> dict:
    return json.loads(_marks_path(repo).read_text())


def _event_ids(repo: Path, name: str = f"{AGENT}.jsonl") -> list[str]:
    return [
        json.loads(ln)["id"] for ln in _shard(repo, name).read_text().splitlines() if ln.strip()
    ]


def _put_legacy(repo: Path, key: str, row: dict) -> None:
    doc = _marks_doc(repo)
    doc["data"][key] = row
    _marks_path(repo).write_text(json.dumps(doc))
