"""One table of harness hooks (B-uni-hooks.1-table).

The session-start, pre-compact and prompt hooks were described three times: `hooks
install/uninstall --claude/--gemini` (api/setup), `adopt` (prompt hooks only) and
`launchers.check_settings` (which markers count as ddflow's), and each re-derived the
event, marker, settings file, matcher and refresh flag by hand. `claudehooks.HOOKS` is the
one table they all read now.

The pins below were written against the code BEFORE the table and must stay true after
it: the exact command each hook writes, where it goes, the install/uninstall messages and
the status lines. The one difference is bug Ba0febd9946: the PreCompact install message
said "every operator prompt is recorded".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.api import setup as API
from ddflow.services import adopt as A
from ddflow.services import claudehooks as CH
from ddflow.services import enforce as E
from ddflow.services import launchers as LA

CLAUDE = ".claude/settings.json"
GEMINI = ".gemini/settings.json"


def _old_lines() -> dict[str, str]:
    """The command lines exactly as api/setup and adopt built them before the table."""
    return {
        "session-start": E.command_line(
            "hooks session-start", refresh="ddflow hooks install --claude"
        ),
        "pre-compact": E.command_line("hooks pre-compact", refresh="ddflow hooks install --claude")
        + " || true",
        "prompt-claude": E.command_line(
            "hooks prompt", extra="", refresh="ddflow hooks install --claude"
        )
        + " || true",
        "prompt-gemini": E.command_line(
            "hooks prompt", extra="--gemini", refresh="ddflow hooks install --gemini"
        )
        + " || true",
    }


def _expected_files() -> dict[str, dict]:
    old = _old_lines()

    def cmd(c: str) -> dict:
        return {"type": "command", "command": c}

    return {
        CLAUDE: {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup|resume|clear|compact",
                        "hooks": [cmd(old["session-start"])],
                    }
                ],
                "PreCompact": [{"hooks": [cmd(old["pre-compact"])]}],
                "UserPromptSubmit": [{"hooks": [cmd(old["prompt-claude"])]}],
            }
        },
        GEMINI: {"hooks": {"BeforeAgent": [{"hooks": [cmd(old["prompt-gemini"])]}]}},
    }


def _unstamped(command: str) -> str:
    """The command line inside the stamped region every ddflow hook command is written as
    (B-uni-compat-artifacts.4): the stamp is asserted here, the pins below are about the line."""
    lines = command.rstrip("\n").split("\n")
    assert lines[0].startswith("# ddflow:begin hooks/") and " fmt=" in lines[0], command
    assert lines[-1].startswith("# ddflow:end hooks/"), command
    return "\n".join(lines[1:-1])


def _read(repo: Path, rel: str) -> dict:
    data = json.loads((repo / rel).read_text("utf-8"))
    for groups in data.get("hooks", {}).values():
        for group in groups:
            for hook in group["hooks"]:
                if "# ddflow:begin" in hook["command"]:
                    hook["command"] = _unstamped(hook["command"])
    return data


def test_install_writes_the_pinned_commands_events_and_matchers(repo: Path) -> None:
    out = API.hooks(repo, action="install", claude=True, gemini=True)
    assert out.exit == 0, out.data
    for rel, want in _expected_files().items():
        assert _read(repo, rel) == want, rel


def test_install_and_uninstall_messages_are_pinned(repo: Path) -> None:
    c, g = repo / CLAUDE, repo / GEMINI
    note = E.redirect_note(_old_lines()["session-start"])
    want = [
        f"added a SessionStart hook to {c}: every session starts with the ddflow brief",
        *([note] if note else []),
        # Ba0febd9946: this line used to say "every operator prompt is recorded".
        f"added a PreCompact hook to {c}: the session's state is recorded before every compaction",
        f"added a UserPromptSubmit hook to {c}: every operator prompt is recorded",
        f"added a BeforeAgent hook to {g}: every operator prompt is recorded",
    ]
    assert API.hooks(repo, action="install", claude=True, gemini=True).data["message"] == "\n".join(
        want
    )
    again = [
        f"the ddflow SessionStart hook is already in {c}",
        *([note] if note else []),
        f"the ddflow PreCompact hook is already in {c}",
        f"the ddflow UserPromptSubmit hook is already in {c}",
        f"the ddflow BeforeAgent hook is already in {g}",
    ]
    assert API.hooks(repo, action="install", claude=True, gemini=True).data["message"] == "\n".join(
        again
    )
    gone = [
        f"removed the ddflow SessionStart hook from {c}",
        f"removed the ddflow PreCompact hook from {c}",
        f"removed the ddflow UserPromptSubmit hook from {c}",
        f"removed the ddflow BeforeAgent hook from {g}",
    ]
    assert API.hooks(repo, action="uninstall", claude=True, gemini=True).data[
        "message"
    ] == "\n".join(gone)
    assert _read(repo, CLAUDE) == {} and _read(repo, GEMINI) == {}
    none = [
        f"no ddflow SessionStart hook in {c}",
        f"no ddflow PreCompact hook in {c}",
        f"no ddflow UserPromptSubmit hook in {c}",
        f"no ddflow BeforeAgent hook in {g}",
    ]
    assert API.hooks(repo, action="uninstall", claude=True, gemini=True).data[
        "message"
    ] == "\n".join(none)


def test_only_the_named_harness_is_touched(repo: Path) -> None:
    API.hooks(repo, action="install", gemini=True)
    assert not (repo / CLAUDE).exists()
    assert _read(repo, GEMINI) == _expected_files()[GEMINI]
    API.hooks(repo, action="install", claude=True)
    assert _read(repo, CLAUDE) == _expected_files()[CLAUDE]


def test_status_lines_are_pinned(repo: Path) -> None:
    before = API.hooks(repo, action="status").data["message"]
    assert (
        "Claude Code SessionStart hook: not installed (`ddflow hooks install --claude` puts "
        "the brief in every session)\n"
        "Claude Code PreCompact hook: not installed (`ddflow hooks install --claude`)\n"
        "prompt capture hook: Claude Code: not installed; Gemini CLI: not installed"
    ) in before
    API.hooks(repo, action="install", claude=True, gemini=True)
    after = API.hooks(repo, action="status")
    assert (
        "Claude Code SessionStart hook: installed\n"
        "Claude Code PreCompact hook: installed\n"
        "prompt capture hook: Claude Code: installed; Gemini CLI: installed"
    ) in after.data["message"]
    assert after.data["session_hook"] is True


def test_status_says_unknown_for_a_settings_file_it_cannot_parse(repo: Path) -> None:
    (repo / ".claude").mkdir()
    (repo / CLAUDE).write_text("{not json", "utf-8")
    msg = API.hooks(repo, action="status").data["message"]
    assert "Claude Code SessionStart hook: UNKNOWN -- " in msg
    assert "Claude Code PreCompact hook: UNKNOWN -- " in msg
    assert "prompt capture hook: Claude Code: UNKNOWN -- " in msg
    assert "Gemini CLI: not installed" in msg


def test_adopt_writes_the_same_prompt_hooks_as_hooks_install(repo: Path) -> None:
    msgs = A._install_prompt_hooks(repo, ["claude", "gemini"])
    assert msgs == [
        f"added a UserPromptSubmit hook to {repo / CLAUDE}: every operator prompt is recorded",
        f"added a BeforeAgent hook to {repo / GEMINI}: every operator prompt is recorded",
    ]
    files = _expected_files()
    assert _read(repo, CLAUDE) == {
        "hooks": {"UserPromptSubmit": files[CLAUDE]["hooks"]["UserPromptSubmit"]}
    }
    assert _read(repo, GEMINI) == files[GEMINI]
    assert A._install_prompt_hooks(repo, ["codex"]) == []


def test_adopt_reports_an_unparseable_settings_file_and_goes_on(repo: Path) -> None:
    (repo / ".claude").mkdir()
    (repo / CLAUDE).write_text("{not json", "utf-8")
    msgs = A._install_prompt_hooks(repo, ["claude", "gemini"])
    assert msgs[0].startswith("prompt hook not installed: ")
    assert (
        msgs[1] == f"added a BeforeAgent hook to {repo / GEMINI}: every operator prompt is recorded"
    )


# --- the table itself ------------------------------------------------------------------


def test_the_table_names_every_harness_hook_once() -> None:
    keys = [(h.agent, h.name) for h in CH.HOOKS]
    assert keys == [
        ("claude", "session-start"),
        ("claude", "pre-compact"),
        ("claude", "prompt"),
        ("gemini", "prompt"),
    ]
    assert len({(h.file, h.event) for h in CH.HOOKS}) == len(CH.HOOKS)
    for h in CH.HOOKS:
        # The marker is the subcommand the hook runs: `ddflow hooks <name>`.
        assert h.marker == f"hooks {h.name}"
        assert CH.spec(h.agent, h.name) is h
    assert CH.MARKER == CH.spec("claude", "session-start").marker
    assert CH.PRECOMPACT_MARKER == CH.spec("claude", "pre-compact").marker
    assert CH.PROMPT_MARKER == CH.spec("claude", "prompt").marker
    assert CH.GEMINI_SETTINGS == CH.spec("gemini", "prompt").file


@pytest.mark.parametrize("h", CH.HOOKS, ids=lambda h: f"{h.agent}-{h.name}")
def test_launchers_recognise_every_table_hook_with_a_dead_launcher(repo: Path, h) -> None:
    line = CH.command(h)
    for p in LA.recorded_paths(line):
        line = line.replace(p, "/nonexistent-ddflow-venv" + p)
    assert LA.recorded_paths(line), "the line records no launcher, so this proves nothing"
    CH.install(repo, line, event=h.event, marker=h.marker, matcher=h.matcher, rel=h.file)
    found = LA.check_settings(repo)
    assert len(found) == 1, found
    assert h.file in found[0].where
