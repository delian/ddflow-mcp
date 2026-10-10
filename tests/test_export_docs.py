"""Export documentation pass (B-export-docs): stale text stays gone and the MCP merge
result carries `export_refresh` like the CLI's does."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_raw as _git

from ddflow import config as C
from ddflow.api._base import _load
from ddflow.infra import worktree as W
from ddflow.surfaces.mcp import Server


def test_config_explain_says_redaction_is_applied(repo):
    # bug B42c05fa15b: the knob's text said redaction was "not applied" long after it was.
    cfg = C.Config.load(repo)
    text = next(doc for key, _v, _s, doc in cfg.explain() if key == "export.redact")
    assert "not applied" not in text and "NOT yet" not in text and "arrives with" not in text


def test_config_explain_cli_has_no_stale_redaction_text(repo):
    assert run_cli(repo, "init")[0] == 0
    code, out, _err = run_cli(repo, "config", "--explain")
    assert code == 0
    block = out[out.index("export.redact") :].split("\nexport.", 1)[0]
    assert "not applied" not in block and "NOT yet" not in block


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Billing", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "first", "--globs", "src/**")
    p = repo / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export]\ndocuments = ["roadmap"]\nrefresh = "merge"\n')
    assert run_cli(repo, "export", "roadmap", "--update")[0] == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    assert run_cli(repo, "claim", "T1")[0] == 0
    run_cli(repo, "task", "add", "T2", "--phase", "P1", "--title", "second one", "--globs", "x/**")
    return repo


def test_mcp_merge_result_carries_export_refresh(proj):
    tree = W.load_path(proj, _load(proj)[2].items["T1"].worktree)
    (tree / "src").mkdir(exist_ok=True)
    (tree / "src" / "a.py").write_text("x = 1\n")
    _git(tree, "add", "src/a.py")
    _git(tree, "commit", "-qm", "work")
    reply = Server(proj).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_merge", "arguments": {"id": "T1", "allow_dirty": True}},
        }
    )["result"]
    body = json.loads(reply["content"][0]["text"])
    assert body["export_refresh"]["changed"] == ["ROADMAP.md"], body
