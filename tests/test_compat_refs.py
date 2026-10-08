"""Stale references (D-compat): the commands and tools a project's own files name.

A renamed command keeps working through an alias, but a settings hook, a driver doc or an
AGENTS.md block that names the old one keeps saying it. `services.compat_refs` finds those
and resolves each: ok, deprecated (with the replacement) or unknown; ddflow's own regions
are rewritten and re-stamped, the project's own text gets a proposal only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.fsio import Managed
from ddflow.services import claudehooks as CH
from ddflow.services import compat_refs as C

VOCAB = C.Vocabulary(
    commands=frozenset(
        {
            ("next",),
            ("claim",),
            ("research",),
            ("gate",),
            ("gate", "record"),
            ("gate", "status"),
            ("docs",),
            ("docs", "show"),
            ("hooks",),
            ("hooks", "install"),
            ("hooks", "session-start"),
        }
    ),
    tools=frozenset({"ddflow_next", "ddflow_gate_record"}),
    command_aliases={
        ("doc",): C.Renamed("docs", "0.1.17", "1.0"),
        ("gate", "rec"): C.Renamed("record", "0.1.17", "1.0"),
        ("cl",): C.Renamed("claim", "0.1.17", "1.0"),
    },
    tool_aliases={"ddflow_old_next": C.Renamed("ddflow_next", "0.1.17", "1.0")},
)


def _refs(line: str, *, code: bool = False) -> list[tuple[str, str, str]]:
    return [(r.text, r.status, r.replacement) for r in C.refs_in(line, VOCAB, code=code)]


# -- resolving names ------------------------------------------------------------------


def test_a_current_command_is_ok_and_a_second_word_counts_only_after_a_group():
    assert _refs("run `ddflow gate record X --outcome passed`") == [("gate record", "ok", "")]
    assert _refs("`ddflow claim B1 gate`") == [("claim", "ok", "")]


def test_global_options_before_the_command_are_skipped():
    assert _refs("`ddflow --agent U6 --json gate status`") == [("gate status", "ok", "")]


def test_an_old_group_name_a_leaf_alias_and_a_command_alias_are_deprecated():
    assert _refs("`ddflow doc show`") == [("doc show", "deprecated", "docs show")]
    assert _refs("`ddflow gate rec X`") == [("gate rec", "deprecated", "gate record")]
    assert _refs("`ddflow cl B1`") == [("cl", "deprecated", "claim")]


def test_unknown_commands_and_tools_are_unknown():
    assert _refs("`ddflow frobnicate`") == [("frobnicate", "unknown", "")]
    assert _refs("`ddflow gate bogus`") == [("gate bogus", "unknown", "")]
    got = [(r.text, r.status) for r in C.refs_in("ddflow_nope and ddflow_next", VOCAB, code=False)]
    assert got == [("ddflow_nope", "unknown"), ("ddflow_next", "ok")]


def test_a_deprecated_tool_names_its_replacement():
    (ref,) = C.refs_in("call ddflow_old_next first", VOCAB, code=False)
    assert (ref.status, ref.replacement) == ("deprecated", "ddflow_next")


def test_prose_is_not_a_reference_but_a_backticked_command_is():
    assert _refs("ddflow is a work queue") == []
    assert _refs("see the ddflow frobnicate story") == []


def test_placeholders_paths_and_comments_are_not_commands():
    assert _refs("`ddflow <command> --help`") == []
    assert _refs("`ddflow $CMD`") == []
    assert _refs("# ddflow nonsense in a comment", code=True) == []
    assert _refs("ddflow next  # ddflow nonsense", code=True) == [("next", "ok", "")]


def test_command_position_in_a_shell_line():
    assert _refs('then exec "/opt/bin/ddflow" hooks session-start || true', code=True) == [
        ("hooks session-start", "ok", "")
    ]
    assert _refs("PYTHONPATH=x exec python -m ddflow doc show", code=True) == [
        ("doc show", "deprecated", "docs show")
    ]
    assert _refs('echo "ddflow: not found, so skipped" >&2', code=True) == []
    assert _refs("  entry: ddflow gate bogus", code=True) == [("gate bogus", "unknown", "")]


# -- scanning a project ---------------------------------------------------------------


def _managed_md(name: str, body: str) -> str:
    return "# Rules\n\nmine: `ddflow doc show`\n\n" + Managed(name).render(body) + "\nmore\n"


def test_scan_tells_managed_text_from_the_projects_own(tmp_path):
    body = "Run `ddflow doc show` and ddflow_old_next.\n"
    (tmp_path / "AGENTS.md").write_text(_managed_md("rules/work-queue", body))
    found = C.scan(tmp_path, VOCAB)
    first = next(f for f in found if f.line == 3)
    assert first.managed is False and "yours to change" in first.describe()
    assert first.proposal == "replace `doc show` with `docs show`"
    assert all(f.managed for f in found if f.line > 3)
    assert {f.ref.text for f in found} == {"doc show", "ddflow_old_next"}
    assert all(f.where.startswith("AGENTS.md:") for f in found)


def test_a_region_a_person_edited_is_theirs(tmp_path):
    text = _managed_md("rules/work-queue", "Run `ddflow gate record`.\n")
    text = text.replace("Run `ddflow gate record`.", "Run `ddflow gate rec`.")  # hand edit
    (tmp_path / "AGENTS.md").write_text(text)
    (f,) = [f for f in C.scan(tmp_path, VOCAB) if f.ref.text == "gate rec"]
    assert f.managed is False


def test_scan_reads_the_pre_commit_config_slash_commands_and_driver_docs(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text("repos:\n  - entry: ddflow gate bogus\n")
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "go.md").write_text("Use `ddflow cl B1`.\n")
    drivers = tmp_path / "docs" / "ddflow" / "drivers"
    drivers.mkdir(parents=True)
    (drivers / "d.md").write_text("```sh\nddflow doc show\n```\n")
    got = {(f.path, f.ref.status) for f in C.scan(tmp_path, VOCAB)}
    assert got == {
        (".pre-commit-config.yaml", "unknown"),
        (".claude/commands/go.md", "deprecated"),
        ("docs/ddflow/drivers/d.md", "deprecated"),
    }


def test_the_report_makes_a_broken_managed_reference_a_problem(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        _managed_md("rules/work-queue", "Run `ddflow nosuch` and `ddflow doc show`.\n")
    )
    problems, notes = C.report(C.scan(tmp_path, VOCAB))
    assert len(problems) == 1 and "AGENTS.md:6" in problems[0] and "nosuch" in problems[0]
    assert any("`doc show` is deprecated since 0.1.17" in n for n in notes)


# -- rewriting ------------------------------------------------------------------------


def test_rewrite_changes_only_managed_regions_restamps_them_and_backs_up(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    path = tmp_path / "AGENTS.md"
    path.write_text(_managed_md("rules/work-queue", "Run `ddflow doc show` now.\n"))
    done = C.rewrite(tmp_path, VOCAB, C.scan(tmp_path, VOCAB))
    text = path.read_text()
    assert done == ["rewrote 1 reference(s) in AGENTS.md"]
    assert "mine: `ddflow doc show`" in text  # the project's own text: untouched
    assert "Run `ddflow docs show` now." in text
    assert Managed("rules/work-queue").state(text) == "current"  # re-stamped, not 'edited'
    assert list((tmp_path / ".ddflow" / "backups").glob("*/manifest.json"))
    assert C.report(C.scan(tmp_path, VOCAB))[0] == []
    assert [f.ref.text for f in C.scan(tmp_path, VOCAB)] == ["doc show"]  # only the proposal


def test_rewrite_leaves_unknown_names_and_user_text_alone(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    path = tmp_path / "AGENTS.md"
    before = _managed_md("rules/work-queue", "Run `ddflow nosuch`.\n")
    path.write_text(before)
    assert C.rewrite(tmp_path, VOCAB, C.scan(tmp_path, VOCAB)) == []
    assert path.read_text() == before


def test_rewrite_refreshes_a_settings_hook_ddflow_wrote_and_not_one_it_did_not(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    ours = Managed("hooks/session-start", open="#", close="").render("ddflow doc show || true\n")
    settings = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": ours}]},
                {"hooks": [{"type": "command", "command": "ddflow doc show"}]},
            ]
        }
    }
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(settings, indent=2) + "\n")
    found = C.scan(tmp_path, VOCAB)
    assert [f.managed for f in found] == [True, False]
    C.rewrite(tmp_path, VOCAB, found)
    cmds = [h["hooks"][0]["command"] for h in json.loads(path.read_text())["hooks"]["SessionStart"]]
    assert "ddflow docs show || true" in cmds[0]
    assert Managed("hooks/session-start", open="#", close="").state(cmds[0]) == "current"
    assert cmds[1] == "ddflow doc show"


# -- the hook's identity ----------------------------------------------------------------


def test_a_hook_is_still_ours_after_the_subcommand_it_runs_is_renamed(tmp_path):
    spec = CH.spec("claude", "session-start")
    CH.install_spec(tmp_path, spec)
    renamed = CH.HookSpec(**{**spec.__dict__, "run": "start-session"})
    assert renamed.marker == spec.marker  # identity unchanged
    CH.install_spec(tmp_path, renamed)
    data = json.loads((tmp_path / CH.CLAUDE_SETTINGS).read_text())
    cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
    assert len(cmds) == 1, "the renamed hook was added beside the old one"
    assert "hooks start-session" in cmds[0] and "hooks session-start" not in cmds[0]
    assert CH.state_spec(tmp_path, renamed) == (True, "")
    assert "removed" in CH.uninstall_spec(tmp_path, renamed)


def test_an_entry_written_before_the_stamp_is_found_by_its_command_text(tmp_path):
    spec = CH.spec("claude", "session-start")
    path = tmp_path / CH.CLAUDE_SETTINGS
    path.parent.mkdir(parents=True)
    legacy = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "ddflow hooks session-start"}]}
            ]
        }
    }
    path.write_text(json.dumps(legacy))
    assert CH.state_spec(tmp_path, spec) == (True, "")
    CH.install_spec(tmp_path, spec)
    data = json.loads(path.read_text())
    assert sum(len(g["hooks"]) for g in data["hooks"]["SessionStart"]) == 1


# -- against this ddflow --------------------------------------------------------------


def test_every_reference_in_the_shipped_templates_names_something_real():
    import ddflow.surfaces.cli  # noqa: F401  (registers the command table)
    from ddflow.surfaces.vocabulary import current_vocabulary

    vocab = current_vocabulary()
    assert vocab is not None and ("gate", "record") in vocab.commands
    root = Path(C.__file__).resolve().parents[1] / "templates"
    bad = []
    for path in sorted(p for p in root.rglob("*") if p.suffix in (".md", ".toml", ".txt", ".json")):
        text = path.read_text("utf-8", errors="replace")
        bad += [
            f.describe() for f in C._text_findings(path.name, text, "template", vocab, code=False)
        ]
    assert bad == []


def test_doctor_reports_a_stale_reference_with_file_and_line(repo):
    (repo / "AGENTS.md").write_text("# a\n\nrun `ddflow frobnicate` first\n")
    _, out, _ = run_cli(repo, "doctor")
    assert "AGENTS.md:3" in out and "frobnicate" in out
    (repo / "AGENTS.md").write_text("# a\n\nrun `ddflow next` first\n")
    _, out, _ = run_cli(repo, "doctor")
    assert "AGENTS.md:3" not in out


def test_a_rewritten_settings_file_keeps_every_other_byte(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    ours = Managed("hooks/session-start", open="#", close="").render("ddflow doc show || true\n")
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    raw = (
        '{\n    "model": "caf\\u00e9",\n  "hooks": {"SessionStart": [{"hooks": '
        '[{"type": "command", "command": ' + json.dumps(ours) + "}]}]},\n"
        '        "note": "keep   me"}\n'
    )
    path.write_text(raw)
    C.rewrite(tmp_path, VOCAB, C.scan(tmp_path, VOCAB))
    after = path.read_text()
    assert (
        after != raw
        and "docs show" in json.loads(after)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    )
    assert '"model": "caf\\u00e9"' in after and '"note": "keep   me"' in after
    assert after.startswith('{\n    "model"')  # not reformatted


def test_a_renamed_subcommand_of_a_renamed_group_is_deprecated_not_unknown():
    vocab = C.Vocabulary(
        commands=frozenset({("check",), ("check", "run")}),
        tools=frozenset(),
        command_aliases={
            ("gate",): C.Renamed("check"),
            ("check", "record"): C.Renamed("run"),
            ("gate", "log"): C.Renamed("run"),
        },
    )
    assert vocab.resolve_command(("gate", "record"))[:2] == ("deprecated", "check run")
    assert vocab.resolve_command(("gate", "log"))[:2] == ("deprecated", "check run")
    assert vocab.resolve_command(("check", "record"))[:2] == ("deprecated", "check run")


def test_the_live_parser_lists_current_names_and_keeps_aliases_apart():
    import argparse

    from ddflow.api.refs import command_names
    from ddflow.surfaces import registry as R

    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="cmd")
    docs = sub.add_parser("docs")
    docs.add_subparsers(dest="docs_cmd").add_parser("show")
    R.add_command_alias(sub, docs, "doc", R.Alias("command", "doc", "docs", "0.1.17"))
    commands, aliases = command_names(root)
    assert commands == {("docs",), ("docs", "show")}  # the old word is not a current command
    assert aliases[("doc",)].new == "docs"
    vocab = C.Vocabulary(commands=commands, tools=frozenset(), command_aliases=aliases)
    assert vocab.resolve_command(("doc", "show"))[:2] == ("deprecated", "docs show")


def test_installing_a_renamed_hook_twice_adds_nothing(tmp_path):
    spec = CH.spec("claude", "session-start")
    renamed = CH.HookSpec(**{**spec.__dict__, "run": "start-session"})
    for _ in range(3):
        CH.install_spec(tmp_path, renamed)
    data = json.loads((tmp_path / CH.CLAUDE_SETTINGS).read_text())
    assert sum(len(g["hooks"]) for g in data["hooks"]["SessionStart"]) == 1


def test_rewrite_keeps_what_stands_between_the_words_of_a_command(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    path = tmp_path / "AGENTS.md"
    path.write_text(
        _managed_md("rules/work-queue", "Run `ddflow  doc  show` or `ddflow doc --long show`.\n")
    )
    C.rewrite(tmp_path, VOCAB, C.scan(tmp_path, VOCAB))
    text = path.read_text()
    assert "`ddflow  docs  show`" in text and "`ddflow docs --long show`" in text
    assert Managed("rules/work-queue").state(text) == "current"


def test_a_quoted_value_of_a_global_option_is_skipped_whole():
    assert _refs('entry: ddflow --repo "my repo" gate bogus', code=True) == [
        ("gate bogus", "unknown", "")
    ]
    assert _refs("x: ddflow --reason 'stale refs' doc show", code=True) == [
        ("doc show", "deprecated", "docs show")
    ]


def test_a_hook_is_located_by_its_whole_command_not_a_prefix_another_shares(tmp_path):
    first = Managed("hooks/session-start-extra", open="#", close="").render("ddflow next\n")
    second = Managed("hooks/session-start", open="#", close="").render("ddflow doc show\n")
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"command": first}, {"command": second}]}]}},
            indent=2,
        )
        + "\n"
    )
    (f,) = C.scan(tmp_path, VOCAB)
    assert (
        f.line
        == path.read_text()
        .splitlines()
        .index(next(x for x in path.read_text().splitlines() if "session-start " in x))
        + 1
    )


def test_a_stale_launcher_in_a_renamed_hook_is_still_reported(tmp_path, monkeypatch):
    from ddflow.services import launchers as L

    spec = CH.HookSpec(**{**CH.spec("claude", "session-start").__dict__, "run": "start-session"})
    CH.install_spec(tmp_path, spec)
    path = tmp_path / CH.CLAUDE_SETTINGS
    data = json.loads(path.read_text())
    cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    data["hooks"]["SessionStart"][0]["hooks"][0]["command"] = cmd.replace(
        'if [ -x "', 'if [ -x "/gone/'
    )
    path.write_text(json.dumps(data))
    seen = []
    monkeypatch.setattr(L, "check_command", lambda where, command, fix, path="": seen.append(where))
    L.check_settings(tmp_path)
    assert seen, "a hook whose command text no longer says `hooks session-start` was skipped"


def test_installing_a_renamed_spec_over_an_unstamped_entry_leaves_one_stamped_hook(tmp_path):
    spec = CH.spec("claude", "session-start")
    renamed = CH.HookSpec(**{**spec.__dict__, "run": "start-session"})
    path = tmp_path / CH.CLAUDE_SETTINGS
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "ddflow hooks session-start"}]}
                    ]
                }
            }
        )
    )
    CH.install_spec(tmp_path, renamed)
    cmds = [
        h["command"]
        for g in json.loads(path.read_text())["hooks"]["SessionStart"]
        for h in g["hooks"]
    ]
    assert len(cmds) == 1 and "hooks start-session" in cmds[0]
    assert Managed("hooks/session-start", open="#", close="").owns(cmds[0])


def test_scan_reads_the_macros_in_the_projects_config(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        '[[macro]]\nname = "m"\nprompt = "Run `ddflow doc show` first."\ntools = ["ddflow_old_next"]\n'
    )
    got = {(f.artifact, f.ref.text) for f in C.scan(tmp_path, VOCAB)}
    assert got == {("config macro", "doc show"), ("config macro", "ddflow_old_next")}


def test_the_same_managed_hook_under_two_events_is_found_twice_and_rewritten(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    ours = Managed("hooks/session-start", open="#", close="").render("ddflow doc show\n")
    hook = {"hooks": [{"type": "command", "command": ours}]}
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"SessionStart": [hook], "PreCompact": [hook]}}, indent=2))
    found = C.scan(tmp_path, VOCAB)
    assert len(found) == 2 and found[0].line != found[1].line
    C.rewrite(tmp_path, VOCAB, found)
    assert C.scan(tmp_path, VOCAB) == []
    assert path.read_text().count("ddflow docs show") == 2


def test_scan_reads_an_ejected_mcp_instructions(tmp_path):
    prompts = tmp_path / ".ddflow" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "mcp_instructions.md").write_text(
        "Call ddflow_old_next first, then `ddflow cl B1`.\n"
    )
    got = {(f.artifact, f.ref.text) for f in C.scan(tmp_path, VOCAB)}
    assert got == {("ejected prompt", "ddflow_old_next"), ("ejected prompt", "cl")}
