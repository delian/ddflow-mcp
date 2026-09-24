"""MCP is a second door onto one implementation — these tests pin that it stays so."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard.surfaces.mcp import TOOLS, _schema, serve


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(
        repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--globs", "src/auth/*"
    )
    return repo


def rpc(repo, messages):
    inp = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    out = io.StringIO()
    serve(repo, stdin=inp, stdout=out)
    return [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]


def test_handshake_echoes_a_known_protocol(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        ],
    )
    assert r[0]["result"]["protocolVersion"] == "2025-03-26"


def test_handshake_falls_back_for_an_unknown_protocol(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        ],
    )
    assert r[0]["result"]["protocolVersion"] in ("2025-06-18",)


def test_every_tool_has_a_valid_schema_and_a_real_description():
    for name, spec in TOOLS.items():
        schema = _schema(spec)
        assert schema["type"] == "object"
        assert len(spec["description"]) > 40, f"{name} needs a usable description"
        for prop, body in schema["properties"].items():
            assert body["type"] in ("string", "integer", "boolean", "number")
            assert body["description"], f"{name}.{prop} is undocumented"


def test_tools_list_matches_the_registry(proj):
    r = rpc(proj, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert {t["name"] for t in r[0]["result"]["tools"]} == set(TOOLS)


def test_a_tool_call_returns_the_cli_result(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_next", "arguments": {"phase": "P1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["ready"][0]["id"] == "P1.T1"


def test_nothing_to_do_is_a_result_not_an_error(proj):
    """Exit 2 must reach the model as readable content, never as a transport error —
    an agent that sees an error retries; an agent that sees 'nothing ready' moves on."""
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="other")
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_next", "arguments": {"phase": "P1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["isError"] is False
    assert res["_meta"]["exit"] == 2


def test_a_refused_claim_reaches_the_model_as_content(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="holder")
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_claim", "arguments": {"id": "P1.T1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["_meta"]["exit"] == 3
    assert "held by" in res["content"][0]["text"]


def test_unknown_tool_is_an_error_result_listing_alternatives(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_teleport", "arguments": {}},
            }
        ],
    )
    assert r[0]["result"]["isError"] is True
    assert "orchard_brief" in r[0]["result"]["content"][0]["text"]


def test_notifications_get_no_reply(proj):
    r = rpc(proj, [{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert r == [], "a notification must not produce a response frame"


def test_malformed_json_gets_a_parse_error_not_a_crash(proj):
    out = io.StringIO()
    serve(proj, stdin=io.StringIO("{not json\n"), stdout=out)
    assert json.loads(out.getvalue())["error"]["code"] == -32700


def test_resources_are_readable(proj):
    r = rpc(
        proj,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": "orchard://board"},
            },
        ],
    )
    assert len(r[0]["result"]["resources"]) >= 3
    assert "P1" in r[1]["result"]["contents"][0]["text"]


def test_server_writes_nothing_but_frames_to_stdout(proj):
    """A stray print corrupts the stream and the client sees a hung server."""
    out = io.StringIO()
    serve(
        proj,
        stdin=io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "orchard_board", "arguments": {}},
                }
            )
            + "\n"
        ),
        stdout=out,
    )
    for line in out.getvalue().splitlines():
        json.loads(line)  # every line must be a valid JSON-RPC frame


def test_a_notification_produces_no_frame_so_a_client_must_not_wait(proj):
    """The deadlock shape: a notification correctly gets no reply, and a client that
    reads one anyway blocks forever while both processes sit at 0% CPU looking healthy.

    Caught by the orchestration scenario — the first thing here to send a notification
    at all. The server was right; the client was wrong; the test pins the contract so a
    future client cannot get it wrong silently.
    """
    out = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ],
    )
    ids = [m.get("id") for m in out]
    assert ids == [1, 2], (
        f"expected exactly two frames (the notification must produce none), got {ids}"
    )


def test_every_cli_command_an_agent_needs_has_an_mcp_tool(proj):
    """An MCP-only agent must not be told to do something MCP cannot express.

    `orchard update --globs` is the sharp case: the canonical driver instructs the
    agent to widen its declared globs BEFORE writing outside its claim, and without
    the tool that instruction was impossible to follow over MCP.
    """
    needed = {
        "show": "orchard_show",
        "update": "orchard_update",
        "release": "orchard_release",
        "block": "orchard_block",
        "next": "orchard_next",
        "claim": "orchard_claim",
        "merge": "orchard_merge",
        "complete": "orchard_complete",
        "brief": "orchard_brief",
        "doctor": "orchard_doctor",
    }
    missing = [cli for cli, tool in needed.items() if tool not in TOOLS]
    assert not missing, f"CLI commands with no MCP tool: {missing}"


def test_the_new_tools_round_trip(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "orchard_update",
                    "arguments": {"id": "P1.T1", "globs": "src/widened/*"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "orchard_show", "arguments": {"id": "P1.T1"}},
            },
        ],
    )
    assert r[0]["result"]["isError"] is False, r[0]["result"]["content"][0]["text"]
    shown = json.loads(r[1]["result"]["content"][0]["text"])
    assert shown["globs"] == ["src/widened/*"], shown["globs"]


def test_starting_the_server_does_not_modify_the_repository(repo):
    """A handshake is a read. It must not leave anything behind.

    It used to create `.orchard/events/` merely by constructing the event log, which
    (a) littered any repository an agent merely connected to, and (b) made the
    "is this project adopted?" check answer yes about a directory the server had just
    created itself.
    """
    before = sorted(p.name for p in repo.iterdir())
    rpc(
        repo,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    assert sorted(p.name for p in repo.iterdir()) == before, "the handshake wrote to the repo"
    assert not (repo / ".orchard").exists()


def test_an_unadopted_repo_is_told_to_set_up_even_after_a_handshake(repo):
    out = rpc(
        repo,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ],
    )
    text = out[0]["result"]["instructions"]
    assert "does not use Orchard yet" in text
    assert "orchard_setup" in text


# -- workflow commands, exposed as MCP prompts ----------------------------------------


def test_the_prompts_capability_is_advertised(proj):
    """Omitting it means a spec-respecting client never calls prompts/list, so the
    commands exist and are unreachable — indistinguishable, from the operator's side,
    from not having written them."""
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ],
    )
    assert "prompts" in r[0]["result"]["capabilities"]


def test_every_workflow_command_is_listed_with_a_usable_description(proj):
    from orchard.services.prompts import COMMANDS

    r = rpc(proj, [{"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}])
    listed = {p["name"]: p for p in r[0]["result"]["prompts"]}
    assert set(listed) == set(COMMANDS), f"listed {sorted(listed)}"
    for name, p in listed.items():
        assert p["title"], name
        assert len(p["description"]) > 60, f"{name}: description too thin to choose by"
        for arg in p["arguments"]:
            assert arg["required"] is False, (
                f"{name}.{arg['name']} is required; a slash command with a mandatory "
                f"argument fails when invoked bare, which is how they are invoked"
            )


def test_a_command_renders_with_and_without_its_optional_argument(proj):
    for args in ({}, {"scope": "the parser"}):
        r = rpc(
            proj,
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "prompts/get",
                    "params": {"name": "bug-hunt", "arguments": args},
                }
            ],
        )
        assert "error" not in r[0], r[0].get("error")
        text = r[0]["result"]["messages"][0]["content"]["text"]
        assert "probe" in text and "regression test" in text
        if args:
            assert "the parser" in text
        assert "{{" not in text and "{%" not in text, "template syntax leaked"


def test_every_shipped_command_renders_cleanly(proj):
    from orchard.services.prompts import COMMANDS

    for name in COMMANDS:
        r = rpc(
            proj,
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "prompts/get",
                    "params": {"name": name, "arguments": {}},
                }
            ],
        )
        assert "error" not in r[0], f"{name}: {r[0].get('error')}"
        text = r[0]["result"]["messages"][0]["content"]["text"]
        assert len(text) > 400, f"{name} rendered suspiciously short"
        assert "{{" not in text and "{%" not in text, f"{name}: template syntax leaked"


def test_an_unknown_command_is_an_error_naming_the_known_ones(proj):
    r = rpc(
        proj,
        [{"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "not-a-command"}}],
    )
    assert "error" in r[0]
    assert "code-clean" in r[0]["error"]["message"]


def test_all_tests_names_the_projects_own_configured_suites(proj):
    """A generic list the reader has to translate is worth less than the real one."""
    (proj / ".orchard" / "config.toml").write_text(
        '[gate.integration_tests]\ncommand = "pytest tests/integration -q"\n\n'
        '[gate.e2e_tests]\ncommand = "npm run test:e2e"\n'
    )
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {"name": "all-tests", "arguments": {}},
            }
        ],
    )
    text = r[0]["result"]["messages"][0]["content"]["text"]
    assert "integration_tests" in text and "e2e_tests" in text, text[:600]


def test_a_project_can_override_a_shipped_command(proj):
    """The workflows ship as text precisely so a project can rewrite one without
    touching code."""
    d = proj / ".orchard" / "prompts" / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / "code-clean.md").write_text("OUR OWN CLEANUP PROCEDURE\n")
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {"name": "code-clean", "arguments": {}},
            }
        ],
    )
    assert "OUR OWN CLEANUP PROCEDURE" in r[0]["result"]["messages"][0]["content"]["text"]
