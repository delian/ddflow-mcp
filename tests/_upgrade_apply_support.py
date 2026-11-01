"""Shared fixture and helpers for the tests/test_upgrade_apply_*.py modules.

The old-version project is the one a real ddflow 0.1.3 built (tests/fixtures/releases/0.1.3).
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli

import ddflow.surfaces.cli  # noqa: F401 -- registers the command table the reference check needs
from ddflow.api._base import _load
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import upgrade_apply as UA

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
DRIVER = "docs/ddflow/drivers/implement-phase.md"


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


def go(repo: Path, categories: Any = None, **kw: Any) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    if "config_changes" in kw:  # the policy is the knob: plan and apply both read it
        cfg.upgrade.config_changes = kw.pop("config_changes")
    return UA.apply(repo, log, cfg, st, categories=categories, agent="upgrader", **kw)


def upgrades(repo: Path) -> list[dict[str, Any]]:
    return fold(EventLog(repo, "upgrader").read_all(), strict=False).upgrades


def tree(repo: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in repo.rglob("*")
        if p.is_file() and ".git/" not in str(p) and "/.ddflow/local" not in str(p)
    }


def ids(body: dict[str, Any], status: str) -> list[str]:
    return [r["id"] for r in body["results"] if r["status"] == status]


def cli(repo: Path, *argv: str) -> tuple[int, str, str]:
    return run_cli(repo, *argv)


def set_knob(repo: Path, key: str, value: str) -> None:
    assert run_cli(repo, "config", "--set", key, value)[0] == 0


def backups(repo: Path) -> list[str]:
    root = repo / ".ddflow" / "backups"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
