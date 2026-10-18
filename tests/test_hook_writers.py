"""Hook config writers chosen by the descriptor's style, not by agent name."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import harnessreg as HR
from ddflow.services import hookwriters as HW


def _doc(repo: Path, rel: str) -> dict:
    return json.loads((repo / rel).read_text())


def test_a_json_groups_agent_gets_one_hook_per_installed_event(repo):
    code, out, err = run_cli(repo, "hooks", "install", "--harness", "codex")
    assert code == 0, err
    hooks = _doc(repo, ".codex/hooks.json")["hooks"]
    h = HR.get("codex")
    assert set(hooks) == {h.hooks.events[e] for e in HW.INSTALLED}
    cmd = hooks[h.hooks.events["prompt"]][0]["hooks"][0]["command"]
    assert "hooks run prompt --harness codex" in cmd


def test_install_is_idempotent_and_keeps_a_foreign_hook(repo):
    path = repo / ".codex" / "hooks.json"
    path.parent.mkdir()
    foreign = {"matcher": "x", "hooks": [{"type": "command", "command": "mine"}]}
    path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [foreign]}, "other": 1}))
    run_cli(repo, "hooks", "install", "--harness", "codex")
    first = path.read_text()
    run_cli(repo, "hooks", "install", "--harness", "codex")
    assert path.read_text() == first
    data = json.loads(first)
    assert data["other"] == 1 and data["hooks"]["UserPromptSubmit"][0] == foreign


def test_uninstall_removes_only_ours(repo):
    run_cli(repo, "hooks", "install", "--harness", "codex")
    path = repo / ".codex" / "hooks.json"
    data = json.loads(path.read_text())
    data["hooks"]["UserPromptSubmit"].append({"hooks": [{"type": "command", "command": "mine"}]})
    path.write_text(json.dumps(data))
    code, _, _ = run_cli(repo, "hooks", "uninstall", "--harness", "codex")
    assert code == 0
    left = json.loads(path.read_text())["hooks"]
    assert list(left) == ["UserPromptSubmit"]
    assert left["UserPromptSubmit"][0]["hooks"][0]["command"] == "mine"


def test_an_unparsable_file_is_refused_and_left_alone(repo):
    path = repo / ".codex" / "hooks.json"
    path.parent.mkdir()
    path.write_text("{not json")
    code, _, _ = run_cli(repo, "hooks", "install", "--harness", "codex")
    assert code != 0 and path.read_text() == "{not json"


def test_a_style_without_a_writer_is_refused_not_ignored(repo):
    code, out, err = run_cli(repo, "hooks", "install", "--harness", "cursor")
    assert code == 3
    assert "cursor" in out + err and not (repo / ".cursor").exists()


def test_every_descriptor_is_either_writable_or_says_why_not():
    for h in HR.load():
        assert bool(HW.specs(h)) == (not HW.unsupported(h)) or any(
            s.agent == h.id for s in HW.CH.HOOKS
        ), h.id


def test_the_dangling_check_reads_a_non_claude_hook_file(repo):
    from ddflow.services import launchers

    run_cli(repo, "hooks", "install", "--harness", "codex")
    path = repo / ".codex" / "hooks.json"
    data = json.loads(path.read_text())
    ev = HR.get("codex").hooks.events["prompt"]
    data["hooks"][ev][0]["hooks"][0]["command"] = (
        data["hooks"][ev][0]["hooks"][0]["command"].replace("ddflow", "/gone/bin/ddflow")
    )
    path.write_text(json.dumps(data))
    assert any(".codex/hooks.json" in d.where for d in launchers.check_settings(repo))
