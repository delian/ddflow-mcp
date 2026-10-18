"""Hook config writers chosen by the descriptor's style, not by agent name."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import harnessreg as HR
from ddflow.services import hookwriters as HW


def _doc(repo: Path, rel: str) -> dict:
    return json.loads((repo / rel).read_text())


def test_a_json_groups_agent_gets_one_hook_per_installed_event(repo):
    code, _out, err = run_cli(repo, "hooks", "install", "--harness", "codex")
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
    code, out, err = run_cli(repo, "hooks", "install", "--harness", "cline")
    assert code == 1
    assert "no hook writer for harness 'cline'" in out + err and not (repo / ".cline").exists()


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
    entry = data["hooks"][ev][0]["hooks"][0]
    entry["command"] = re.sub(r'(\[ -x )"[^"]+"', r'\1"/gone/python"', entry["command"])
    path.write_text(json.dumps(data))
    assert any(".codex/hooks.json" in d.where for d in launchers.check_settings(repo))


def test_cursor_gets_the_flat_list_layout_and_keeps_foreign_entries(repo):
    path = repo / ".cursor" / "hooks.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine"}]}}))
    assert run_cli(repo, "hooks", "install", "--harness", "cursor")[0] == 0
    first = path.read_text()
    assert run_cli(repo, "hooks", "install", "--harness", "cursor")[0] == 0
    assert path.read_text() == first
    hooks = json.loads(first)["hooks"]
    ev = HR.get("cursor").hooks.events["prompt"]
    assert "hooks run prompt --harness cursor" in hooks[ev][0]["command"]
    assert hooks["stop"] == [{"command": "mine"}]
    assert run_cli(repo, "hooks", "uninstall", "--harness", "cursor")[0] == 0
    assert json.loads(path.read_text()) == {"version": 1, "hooks": {"stop": [{"command": "mine"}]}}


def test_copilot_entries_carry_type_and_bash(repo):
    assert run_cli(repo, "hooks", "install", "--harness", "copilot")[0] == 0
    doc = _doc(repo, ".github/hooks/ddflow.json")
    ev = HR.get("copilot").hooks.events["session_start"]
    entry = doc["hooks"][ev][0]
    assert (
        entry["type"] == "command" and "hooks run session_start --harness copilot" in entry["bash"]
    )


def test_flags_and_harness_together_apply_both(repo):
    code, _out, err = run_cli(repo, "hooks", "install", "--claude", "--harness", "codex")
    assert code == 0, err
    assert (repo / ".claude/settings.json").is_file() and (repo / ".codex/hooks.json").is_file()


def test_a_hook_file_outside_the_project_is_not_written():
    from dataclasses import replace

    h = HR.get("codex")
    for bad in ("/etc/hooks.json", "../x/hooks.json", "${HOME}/hooks.json", "~/hooks.json"):
        moved = replace(h, hooks=replace(h.hooks, file=bad))
        assert HW.unsupported(moved), bad


def test_the_descriptor_outranks_the_table():
    from dataclasses import replace

    h = HR.get("claude")
    assert HW.specs(h)
    assert (
        HW.specs(replace(h, hooks=replace(h.hooks, style="none", events={}, inject=(), file="")))
        == ()
    )
