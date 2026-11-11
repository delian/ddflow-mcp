"""B-rules-inventory: every agent's native rule and command files, who loads what, the
always-loaded cost, and rules that restate ddflow-owned workflow."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import rulesinv as RI


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _row(inv: dict, path: str) -> dict:
    return next(r for r in inv["rows"] if r["path"] == path)


def test_frontmatter_parses_scalars_lists_and_bools():
    fm, body = RI.parse_frontmatter(
        '---\ndescription: "Use tabs"\nalwaysApply: false\nglobs: ["*.py", src/**]\n'
        "paths:\n  - a/*.md\n  - b/*.md\n---\nBody here\n"
    )
    assert fm["description"] == "Use tabs"
    assert fm["alwaysApply"] is False
    assert fm["globs"] == ["*.py", "src/**"]
    assert fm["paths"] == ["a/*.md", "b/*.md"]
    assert body == "Body here\n"


def test_frontmatter_absent_or_unclosed_is_empty():
    assert RI.parse_frontmatter("no front\n") == ({}, "no front\n")
    assert RI.parse_frontmatter("---\nkey: v\nnever closed\n")[0] == {}


def test_load_modes_from_frontmatter(tmp_path):
    _w(tmp_path, ".cursor/rules/a.mdc", "---\nalwaysApply: true\n---\nalways rule\n")
    _w(tmp_path, ".cursor/rules/b.mdc", '---\nglobs: ["*.py"]\n---\npy rule\n')
    _w(tmp_path, ".cursor/rules/c.mdc", "---\ndescription: when asked\n---\nx\n")
    _w(tmp_path, ".cursor/rules/d.md", "plain no frontmatter\n")
    inv = RI.inventory(tmp_path)
    assert _row(inv, ".cursor/rules/a.mdc")["load"] == "always"
    b = _row(inv, ".cursor/rules/b.mdc")
    assert b["load"] == "by path" and b["globs"] == ["*.py"]
    assert _row(inv, ".cursor/rules/c.mdc")["load"] == "on request"
    assert _row(inv, ".cursor/rules/d.md")["load"] == "always"
    assert "cursor" in _row(inv, ".cursor/rules/a.mdc")["loaded_by"]


def test_scan_rule_files_names_agents_per_file(tmp_path):
    _w(tmp_path, "AGENTS.md", "# rules\n")
    _w(tmp_path, "CLAUDE.md", "# claude\n")
    rows = RI.scan_rule_files(tmp_path)
    agents = _row({"rows": rows}, "AGENTS.md")["loaded_by"]
    assert "codex" in agents and "cursor" in agents
    claude = _row({"rows": rows}, "CLAUDE.md")["loaded_by"]
    assert "claude" in claude and "codex" not in claude


def test_always_loaded_bytes_and_tokens_per_agent(tmp_path):
    _w(tmp_path, "AGENTS.md", "x" * 400)
    _w(tmp_path, ".cursor/rules/b.mdc", '---\nglobs: ["*.py"]\n---\n' + "y" * 4000)
    inv = RI.inventory(tmp_path)
    cur = inv["agents"]["cursor"]
    assert cur["always_bytes"] == 400
    assert cur["always_tokens_approx"] == 100
    assert inv["tokens_note"].lower().startswith("approximate")
    assert inv["agents"]["codex"]["always_bytes"] == 400


def test_command_dirs_are_on_request(tmp_path):
    _w(tmp_path, ".claude/commands/implement.md", "do it\n")
    inv = RI.inventory(tmp_path)
    row = _row(inv, ".claude/commands/implement.md")
    assert row["kind"] == "command" and row["load"] == "on request"
    assert inv["agents"]["claude"]["always_bytes"] == 0


def test_restated_workflow_noted_naming_the_file(tmp_path):
    _w(
        tmp_path,
        "AGENTS.md",
        "Keep a TODO.md task list.\nLog every issue you find in ISSUES.md.\n"
        "Create a feature branch per task.\nWrite to lessons.md after a surprise.\n",
    )
    inv = RI.inventory(tmp_path)
    topics = {n["topic"] for n in inv["notes"] if n["path"] == "AGENTS.md"}
    assert topics == {"todo list", "issue logging", "branch lifecycle", "lessons file"}


def test_ddflow_managed_block_is_not_a_restatement(tmp_path):
    _w(
        tmp_path,
        "AGENTS.md",
        "<!-- ddflow:begin x -->\ntodo list, lessons file, branch per task\n<!-- ddflow:end x -->\nclean\n",
    )
    assert RI.inventory(tmp_path)["notes"] == []


def test_doctor_note_only_when_an_adopted_agent_sees_nothing(tmp_path):
    _w(tmp_path, "CLAUDE.md", "# only claude reads this\n")
    deltas = tmp_path / "docs/ddflow/drivers/deltas"
    deltas.mkdir(parents=True)
    for name in ("claude-code.md", "codex-cli.md"):
        (deltas / name).write_text("x", encoding="utf-8")
    note = RI.doctor_note(tmp_path)
    assert note and "codex" in note and "claude" in note
    _w(tmp_path, "AGENTS.md", "# shared\n")
    # claude reads AGENTS.md only conditionally but lists it; codex now sees a file too
    assert RI.doctor_note(tmp_path) is None


def test_doctor_note_silent_with_no_rule_files(tmp_path):
    assert RI.doctor_note(tmp_path) is None


def test_ddflow_whole_file_native_rule_is_marked_owned_from_native_rules(tmp_path):
    from ddflow.services.adopt import FORM_WHOLE, NATIVE_RULES

    path = next(r.path for r in NATIVE_RULES.values() if r.form == FORM_WHOLE)
    _w(tmp_path, path, "---\nalwaysApply: true\n---\nKeep a TODO.md task list.\n")
    inv = RI.inventory(tmp_path)
    assert _row(inv, path)["ddflow_owned"] is True
    assert [n for n in inv["notes"] if n["path"] == path] == []


def test_inventory_command_declared_and_runs(tmp_path):
    from ddflow.surfaces.declared import rules as DR

    assert "ddflow_rule_inventory" in DR.BY_TOOL
    _w(tmp_path, "AGENTS.md", "# r\n")
    cmd = DR.BY_TOOL["ddflow_rule_inventory"]
    out = cmd.call(tmp_path, {}, "t")
    assert out.exit == 0 and out.data["count"] >= 1
    assert "AGENTS.md" in cmd.render(out, {})


def test_directory_entries_do_not_descend_into_other_checkouts(tmp_path):
    _w(tmp_path, ".kilo/rules/mine.md", "mine\n")
    _w(tmp_path, ".kilo/worktrees/other/AGENTS.md", "Keep a TODO.md task list.\n")
    inv = RI.inventory(tmp_path)
    paths = {r["path"] for r in inv["rows"]}
    assert ".kilo/rules/mine.md" in paths
    assert not any("worktrees" in p for p in paths)
    assert inv["notes"] == []


def test_directory_entry_files_are_rules_not_cross_agent(tmp_path):
    _w(tmp_path, ".clinerules/style.md", "s\n")
    _w(tmp_path, "AGENTS.md", "a\n")
    inv = RI.inventory(tmp_path)
    assert _row(inv, ".clinerules/style.md")["kind"] == "rule"
    assert _row(inv, "AGENTS.md")["kind"] == "cross-agent"


def test_glob_entries_skip_other_checkouts_too(tmp_path):
    _w(tmp_path, ".cursor/rules/mine.md", "mine\n")
    _w(tmp_path, "worktrees/other/.cursor/rules/theirs.md", "theirs\n")
    _w(tmp_path, "node_modules/p/AGENTS.md", "vendored\n")
    paths = {r["path"] for r in RI.inventory(tmp_path)["rows"]}
    assert ".cursor/rules/mine.md" in paths
    assert not any(p.startswith(("worktrees", "node_modules")) for p in paths)


def test_rule_entry_beats_command_dir_whichever_is_listed_first(tmp_path, monkeypatch):
    _w(tmp_path, ".x/ship.md", "---\nalwaysApply: true\n---\n" + "z" * 40)
    monkeypatch.setattr(
        RI,
        "_surfaces",
        lambda: [("cmdagent", "command", ".x/"), ("ruleagent", "rule", ".x/*.md")],
    )
    row = _row({"rows": RI.scan_rule_files(tmp_path)}, ".x/ship.md")
    assert row["kind"] == "rule" and row["load"] == "always"
    assert row["loaded_by"] == ["cmdagent", "ruleagent"]


def test_doctor_note_fires_when_each_agent_sees_only_its_own_rule_files(tmp_path):
    _w(tmp_path, ".cursor/rules/a.mdc", "---\nalwaysApply: true\n---\nrule a\n")
    _w(tmp_path, "CLAUDE.md", "# only claude\n")
    deltas = tmp_path / "docs/ddflow/drivers/deltas"
    deltas.mkdir(parents=True)
    for name in ("claude-code.md", "cursor.md"):
        (deltas / name).write_text("x", encoding="utf-8")
    note = RI.doctor_note(tmp_path)
    assert note and "cursor sees none of the rule files claude" in note
    assert "claude sees none of the rule files cursor" in note
    assert "claude sees none of the rule files claude" not in note


def test_a_command_dir_agent_does_not_always_load_a_file_a_rule_agent_does(tmp_path, monkeypatch):
    _w(tmp_path, ".x/ship.md", "---\nalwaysApply: true\n---\n" + "z" * 40)
    monkeypatch.setattr(
        RI,
        "_surfaces",
        lambda: [("cmdagent", "command", ".x/"), ("ruleagent", "rule", ".x/*.md")],
    )
    row = _row({"rows": RI.scan_rule_files(tmp_path)}, ".x/ship.md")
    assert row["load_by"] == {"cmdagent": "on request", "ruleagent": "always"}
    monkeypatch.setattr(RI, "_adopted_ids", lambda repo: ["cmdagent", "ruleagent"])
    monkeypatch.setattr(RI.HR, "load", lambda: [_H("cmdagent"), _H("ruleagent")])
    agents = RI.inventory(tmp_path)["agents"]
    assert agents["cmdagent"]["always_bytes"] == 0
    assert agents["ruleagent"]["always_bytes"] > 0


class _H:
    def __init__(self, id):
        self.id = id
