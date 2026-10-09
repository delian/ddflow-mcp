"""Characterization pins for the MCP handshake (B-uc-mcp): the template's variable contract
and the bare-interpreter import `scripts/bump.sh` relies on. Both held before the engine
moved its reads into `api/surf_mcp.py` and must keep holding."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.surfaces import mcp

ROOT = Path(__file__).resolve().parents[1]

# Every variable `mcp_instructions.md` can render from. A new one is a new line here AND a
# default in `mcp._VAR_DEFAULTS` (B153: an unseeded variable broke the handshake of every
# first-time user).
VARS = {
    "adopted", "setup_todo", "companions", "missing_companions", "unregistered_companions",
    "uninstalled_companions", "rules_drift", "unchecked_companions", "actionable_companions",
    "gate_gaps", "recoverable", "ready", "running", "blocked", "open_bugs", "loops",
    "task_pipeline", "tool_tier_note", "require_outcome", "importable", "queue_is_empty",
    "imported_total", "imported_no_globs", "imported_shipped_drift",
}  # fmt: skip


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_unadopted_repository_gets_every_variable_at_its_default(tmp_path: Path) -> None:
    v = mcp._instruction_vars(_git_repo(tmp_path / "r"))
    assert set(v) == VARS
    assert v["adopted"] is False
    assert v["queue_is_empty"] is True and v["require_outcome"] is True
    assert v["setup_todo"] == [] and v["ready"] == 0


def test_defaults_are_not_shared_between_calls(tmp_path: Path) -> None:
    first = mcp._instruction_vars(_git_repo(tmp_path / "r"))
    first["setup_todo"].append("scribble")
    assert mcp._instruction_vars(tmp_path / "r")["setup_todo"] == []


def test_broken_config_still_gets_every_variable(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text("this is [not toml\n", "utf-8")
    v = mcp._instruction_vars(repo)
    assert set(v) == VARS
    assert v["adopted"] is True and v["ready"] == 0


@pytest.mark.parametrize("module", ["ddflow.surfaces.mcp", "ddflow.surfaces.tools._common"])
def test_engine_imports_without_the_template_engine(module: str) -> None:
    """`-S` drops site-packages (jinja2, tomlkit): the engine must still import."""
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import {module}"
    run = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[-800:]
