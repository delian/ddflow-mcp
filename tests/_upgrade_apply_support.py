"""Shared helpers for the tests/test_upgrade_apply_*.py modules (the `old` fixture is in conftest).

Rename map: the former tests/test_upgrade_apply.py (D-small-files-30k split) became four modules,
every test keeping its name:
  test_upgrade_apply_core.py   apply service: backups, policies, partial applies, repairs, the fold
  test_upgrade_apply_docs.py   hand edits to driver docs and rules blocks survive
  test_upgrade_apply_wire.py   the CLI, --json and MCP wire
  test_upgrade_apply_knobs.py  [upgrade] backup / backup_keep / config_changes and settled items
"""

from pathlib import Path
from typing import Any

from _cli_helpers import run_cli

import ddflow.surfaces.cli  # noqa: F401 -- registers the command table the reference check needs
from ddflow.api._base import _load
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import upgrade_apply as UA

DRIVER = "docs/ddflow/drivers/implement-phase.md"


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
