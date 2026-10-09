"""`ddflow hooks run <event> --harness <id>`: one entry point for every agent's hooks.

The old per-event subcommands (`hooks session-start`, `prompt`, `pre-compact`) are aliases of
it, so the aliases and the entry point are pinned to behave the same. What is new and pinned
here: each agent's payload dialect is read into one `HookPayload`, each agent's reply is shaped
by its own emitter, an emitter prints context only where the descriptor says the agent honours
it, and no input makes the hook exit non-zero.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import harnessreg, hookio
from ddflow.surfaces.parsers import workflow as parsers


def _run(repo: Path, *argv: str, stdin: str = "") -> tuple[int, str, str]:
    root = str(Path(__file__).resolve().parents[1])
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": root},
        timeout=120,
    )
    return p.returncode, p.stdout, p.stderr


def _prompts(repo: Path) -> list[dict]:
    out = []
    for shard in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for line in shard.read_text().splitlines():
            ev = json.loads(line)
            if ev["kind"] == "session.prompt":
                out.append(ev)
    return out


# --- the registry and the tables agree ---------------------------------------------------


def test_the_parser_accepts_exactly_the_canonical_events():
    assert parsers._HOOK_EVENTS == harnessreg.CANONICAL_EVENTS


def test_every_command_hook_descriptor_resolves_to_a_normalizer_and_an_emitter():
    seen = 0
    for h in harnessreg.load():
        if h.hooks is None or h.hooks.style not in harnessreg.COMMAND_HOOK_STYLES:
            continue
        seen += 1
        assert h.hooks.normalizer in hookio.NORMALIZERS, h.id
        assert h.hooks.emitter in hookio.EMITTERS, h.id
        assert hookio.plan_for(h.id) is not None, h.id
    assert seen >= 10


def test_no_plan_for_an_agent_without_command_hooks():
    assert hookio.plan_for("aider") is None  # none
    assert hookio.plan_for("opencode") is None  # a plugin, not a command hook
    assert hookio.plan_for("glm") is None  # not verified
    assert hookio.plan_for("no-such-agent") is None


# --- normalizers: each dialect into one payload -------------------------------------------


@pytest.mark.parametrize(
    ("normalizer", "stdin", "want"),
    [
        (
            "claude",
            {"session_id": "s1", "prompt": "hi", "source": "compact", "model": "m", "cwd": "/w"},
            ("s1", "hi", "compact", "m", "/w"),
        ),
        ("claude", {"conversation_id": "c1", "prompt": "hi"}, ("c1", "hi", "", "", "")),
        ("copilot", {"sessionId": "k1", "prompt": "yo", "cwd": "/w"}, ("k1", "yo", "", "", "/w")),
        ("copilot", {"session_id": "k2", "initialPrompt": "go"}, ("k2", "go", "", "", "")),
        (
            "cursor",
            {"conversation_id": "u1", "session_id": "x", "prompt": "p", "workspace_roots": ["/r"]},
            ("u1", "p", "", "", "/r"),
        ),
        (
            "gemini",
            {"session_id": "g1", "prompt": "q", "source": "resume"},
            ("g1", "q", "resume", "", ""),
        ),
        (
            "antigravity",
            {"conversationId": "a1", "workspacePaths": ["/p"], "modelName": "mm"},
            ("a1", None, "", "mm", "/p"),
        ),
        (
            "cline",
            {"taskId": "t1", "userPromptSubmit": {"prompt": "do"}, "workspaceRoots": ["/c"]},
            ("t1", "do", "", "", "/c"),
        ),
        (
            "cascade",
            {"trajectory_id": "z1", "tool_info": {"user_prompt": "w", "cwd": "/z"}},
            ("z1", "w", "", "", "/z"),
        ),
        ("goose", {"session_id": "o1", "message": "m1"}, ("o1", "m1", "", "", "")),
    ],
)
def test_each_dialect_normalizes_to_the_same_payload(normalizer, stdin, want):
    p = hookio.normalize(normalizer, "prompt", "h", json.dumps(stdin))
    assert (p.session_id, p.prompt, p.source, p.model, p.cwd) == want
    assert p.event == "prompt" and p.harness == "h" and p.raw == stdin


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null", '"text"', "{"])
def test_a_payload_that_is_not_an_object_normalizes_to_an_empty_one(raw):
    p = hookio.normalize("claude", "prompt", "claude", raw)
    assert p.prompt is None and p.session_id == "" and p.raw == {}


def test_only_a_non_object_is_malformed_not_an_empty_one():
    assert hookio.normalize("claude", "stop", "c", "{}").malformed is False
    assert hookio.normalize("claude", "stop", "c", "").malformed is False
    assert hookio.normalize("claude", "stop", "c", "[1]").malformed is True
    assert hookio.normalize("claude", "stop", "c", "{").malformed is True


def test_a_prompt_that_is_not_text_is_no_prompt():
    assert hookio.normalize("claude", "prompt", "c", '{"prompt": 5}').prompt is None
    assert hookio.normalize("claude", "prompt", "c", '{"prompt": ""}').prompt is None


# --- emitters: each agent's reply ----------------------------------------------------------


def _emit(harness: str, event: str, text: str) -> str:
    plan = hookio.plan_for(harness)
    assert plan is not None
    return hookio.emit(plan, event, text)


def test_claude_and_codex_print_the_context_as_plain_text():
    assert _emit("claude", "session_start", "BRIEF") == "BRIEF"
    assert _emit("codex", "session_start", "BRIEF") == "BRIEF"
    assert _emit("claude", "prompt", "") == ""


def test_gemini_always_answers_json_and_injects_at_session_start():
    assert _emit("gemini", "prompt", "") == "{}"
    out = json.loads(_emit("gemini", "session_start", "BRIEF"))
    assert out["hookSpecificOutput"]["additionalContext"] == "BRIEF"
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_copilot_cursor_and_cline_use_their_own_context_fields():
    assert json.loads(_emit("copilot", "session_start", "B")) == {"additionalContext": "B"}
    assert json.loads(_emit("cursor", "session_start", "B")) == {"additional_context": "B"}
    assert json.loads(_emit("cline", "prompt", "B")) == {"contextModification": "B"}


def test_context_is_never_printed_where_the_agent_drops_it():
    # Copilot CLI drops a prompt hook's output; Cursor's beforeSubmitPrompt cannot inject;
    # Windsurf Cascade's hooks cannot reach the model at all.
    assert _emit("copilot", "prompt", "B") == ""
    assert _emit("cursor", "prompt", "B") == "{}"
    assert _emit("windsurf", "prompt", "B") == ""
    assert _emit("windsurf", "session_start", "B") == ""


# --- the entry point, end to end ----------------------------------------------------------


def test_run_prompt_records_the_prompt_for_each_dialect(repo):
    run_cli(repo, "init")
    cases = [
        ("claude", {"session_id": "sa", "prompt": "from claude"}),
        ("copilot", {"sessionId": "sb", "prompt": "from copilot"}),
        ("cursor", {"conversation_id": "sc", "prompt": "from cursor"}),
        ("cline", {"taskId": "sd", "userPromptSubmit": {"prompt": "from cline"}}),
    ]
    for harness, payload in cases:
        code, _out, err = _run(
            repo, "hooks", "run", "prompt", "--harness", harness, stdin=json.dumps(payload)
        )
        assert code == 0, err
    got = {p["subject"]: p["data"]["text"] for p in _prompts(repo)}
    assert got == {
        "h-sa": "from claude",
        "h-sb": "from copilot",
        "h-sc": "from cursor",
        "h-sd": "from cline",
    }


@pytest.mark.parametrize("harness", ["claude", "gemini"])
def test_the_prompt_alias_and_the_entry_point_behave_the_same(repo, harness):
    run_cli(repo, "init")
    flag = ["--gemini"] if harness == "gemini" else []
    payload = json.dumps({"session_id": f"s-{harness}", "prompt": "same words"})
    a = _run(repo, "hooks", "prompt", *flag, stdin=payload)
    b = _run(repo, "hooks", "run", "prompt", "--harness", harness, stdin=payload)
    assert a == b
    assert a[0] == 0 and a[1] == ("{}\n" if harness == "gemini" else "")
    # The second call is the same words in the same session: the hook never double-records.
    assert [p["data"]["text"] for p in _prompts(repo)] == ["same words"]


def test_the_session_start_alias_and_the_entry_point_print_the_same_brief(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "the next thing", "--globs", "a.py")
    a = _run(repo, "hooks", "session-start", stdin="{}")
    b = _run(repo, "hooks", "run", "session_start", "--harness", "claude", stdin="{}")
    assert a[0] == b[0] == 0
    assert a[1] == b[1] and "the next thing" in a[1]


def test_session_start_is_shaped_for_the_agent_and_skipped_where_it_cannot_show(repo):
    run_cli(repo, "init")
    code, out, _e = _run(repo, "hooks", "run", "session_start", "--harness", "cursor", stdin="{}")
    assert code == 0 and "ddflow" in json.loads(out)["additional_context"]
    code, out, _e = _run(repo, "hooks", "run", "session_start", "--harness", "gemini", stdin="{}")
    assert "additionalContext" in json.loads(out)["hookSpecificOutput"]
    # Windsurf's hooks cannot reach the model: nothing is built and nothing is printed.
    assert _run(repo, "hooks", "run", "session_start", "--harness", "windsurf", stdin="{}")[:2] == (
        0,
        "",
    )


@pytest.mark.parametrize("event", hookio.CANONICAL)
@pytest.mark.parametrize("raw", ["", "not json", "[1]", '{"prompt": 5}'])
def test_no_event_and_no_input_ever_fails_the_agents_turn(repo, event, raw):
    run_cli(repo, "init")
    code, _out, _err = _run(repo, "hooks", "run", event, "--harness", "claude", stdin=raw)
    assert code == 0


def test_an_unknown_harness_exits_zero_and_says_why_on_stderr(repo):
    run_cli(repo, "init")
    code, out, err = _run(repo, "hooks", "run", "prompt", "--harness", "nonesuch", stdin="{}")
    assert code == 0 and out == ""
    assert "no command-hook descriptor for harness 'nonesuch'" in err


def test_a_plugin_style_agent_has_no_command_hook(repo):
    run_cli(repo, "init")
    code, out, err = _run(repo, "hooks", "run", "prompt", "--harness", "opencode", stdin="{}")
    assert code == 0 and out == "" and "no command-hook descriptor" in err


def test_an_unknown_or_missing_event_exits_zero_and_says_so_on_stderr(repo):
    """argparse would exit 2 here, and exit 2 blocks the turn in Claude Code and Codex."""
    run_cli(repo, "init")
    code, out, err = _run(repo, "hooks", "run", "sparkle", stdin="{}")
    assert code == 0 and out == "" and "unknown event 'sparkle'" in err
    code, out, err = _run(repo, "hooks", "run", stdin="{}")
    assert code == 0 and out == "" and "unknown event ''" in err


def test_the_reply_names_the_event_in_the_agents_own_vocabulary():
    plan = hookio.plan_for("gemini")
    assert plan is not None
    # Gemini's prompt event is BeforeAgent; were it injectable, the reply must say so.
    forced = hookio.Plan(
        plan.harness, plan.normalizer, plan.emitter, ("prompt",), dict(plan.events)
    )
    out = json.loads(hookio.emit(forced, "prompt", "B"))
    assert out["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"


def test_a_failing_emitter_still_exits_zero(repo, monkeypatch):
    from ddflow import api

    def boom(*_a, **_k):
        raise RuntimeError("emitter exploded")

    monkeypatch.setitem(hookio.EMITTERS, "claude", boom)
    out = api.hooks(repo, action="run", event="stop", harness="claude", stdin="{}")
    assert out.data["stdout"] == "" and "emitter exploded" in out.data["note"]


def test_pre_compact_stays_silent_on_stdout_and_notes_a_skip_on_stderr(repo):
    run_cli(repo, "init")
    code, out, err = _run(repo, "hooks", "run", "pre_compact", "--harness", "claude", stdin="{}")
    assert code == 0 and out == ""
    assert err.startswith("ddflow pre-compact:")
    a = _run(repo, "hooks", "pre-compact", stdin="{}")
    assert (a[0], a[1], a[2]) == (code, out, err)


def test_events_without_a_handler_yet_are_accepted_and_silent(repo):
    run_cli(repo, "init")
    for event in ("pre_tool", "post_tool", "stop", "session_end"):
        assert _run(repo, "hooks", "run", event, stdin="{}") == (0, "", "")


def test_install_harness_is_the_claude_and_gemini_flags(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "hooks", "install", "--harness", "claude")[0] == 0
    assert (repo / ".claude" / "settings.json").is_file()
    assert run_cli(repo, "hooks", "install", "--harness", "gemini")[0] == 0
    assert (repo / ".gemini" / "settings.json").is_file()
    assert run_cli(repo, "hooks", "uninstall", "--harness", "claude")[0] == 0


def test_install_harness_for_an_agent_without_a_writer_fails_and_says_so(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "hooks", "install", "--harness", "cursor")
    assert code == 1 and "no hook writer for harness 'cursor' yet" in err
    assert not (repo / ".cursor").exists()
