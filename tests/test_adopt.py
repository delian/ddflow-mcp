"""Adoption into a real project, per agent. These are the paths a new user hits first."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services.adopt import (
    AGENT_TARGETS,
    NATIVE_RULES,
    SHAPE_NONE,
    SHAPE_TOML,
    get_server,
)


def test_every_agent_has_a_delta_document():
    templates = Path(__file__).resolve().parents[1] / "ddflow" / "templates" / "drivers"
    for key, target in AGENT_TARGETS.items():
        assert (templates / "deltas" / target.delta).is_file(), f"{key} has no delta doc"


@pytest.mark.parametrize("agent", sorted(AGENT_TARGETS))
def test_adopt_writes_a_usable_mcp_config(repo, agent):
    rc, out, _err = run_cli(repo, "adopt", "--agents", agent)
    assert rc == 0
    target = AGENT_TARGETS[agent]

    if target.shape == SHAPE_NONE:
        # An agent with no project-level MCP file is still SUPPORTED — the delta doc and
        # AGENTS.md are what make the workflow portable. What must not happen is a file
        # written where the agent will never look, so assert both halves: nothing is
        # created, and the operator is TOLD rather than left to assume it worked.
        assert not target.config or not (repo / target.config).exists()
        assert "no project-level MCP config" in out, (
            f"{agent} has no project MCP file and adopt did not say so: {out!r}"
        )
        return

    cfg = repo / target.config
    assert cfg.is_file(), f"{agent}: {target.config} was not written"
    if target.shape == SHAPE_TOML:
        entry = tomllib.loads(cfg.read_text())["mcp_servers"]["ddflow"]
    else:
        # Looked up through the SAME helper the writer uses. Sniffing the field name
        # ("servers" if present else "mcpServers") passed for a writer that wrote either
        # one, so it could not have caught a server placed under the wrong key.
        entry = get_server(json.loads(cfg.read_text()), target.shape, "ddflow")
    assert entry, f"{agent}: no ddflow entry where {target.shape} says it belongs"
    assert entry["command"], f"{agent}: no launch command"
    # opencode folds the arguments INTO `command` as one array; everyone else splits.
    assert isinstance(entry["command"], str | list)
    assert isinstance(entry.get("args", []), list)


def test_kilo_is_registered_under_the_key_kilo_actually_reads(repo):
    """Kilo reads `mcp` -> {type: "local", command: [...]}, the opencode shape.

    It was registered as `mcpServers` -> {command, args}, and the test above could not
    see it: that test looks the entry up through `get_server` with the SAME shape the
    writer used, and `DOCUMENTED_SHAPES` pins what each shape looks like, not which agent
    gets which. So an agent assigned the wrong shape passed both. Probed against Kilo
    7.2.20 (`kilo mcp list` in a scratch repo): the `mcpServers` file reported "No MCP
    servers configured", the `mcp` file listed the server. Every `adopt --agents kilo`
    until then wrote a file Kilo ignored and reported success. Primary doc:
    https://kilo.ai/docs/automate/mcp/using-in-cli

    Read LITERALLY, not through `get_server`, for the reason above.
    """
    assert run_cli(repo, "adopt", "--agents", "kilo")[0] == 0
    data = json.loads((repo / ".kilo" / "kilo.json").read_text())
    assert "mcpServers" not in data, "Kilo ignores `mcpServers`"
    entry = data["mcp"]["ddflow"]
    assert entry["type"] == "local"
    assert isinstance(entry["command"], list) and entry["command"], entry
    assert "args" not in entry, "Kilo takes the arguments inside `command`"
    assert entry["enabled"] is True


def test_cursor_gets_an_always_applied_project_rule(repo):
    """Cursor's precedence puts Project Rules ABOVE AGENTS.md.

    Writing only AGENTS.md would leave the queue discipline as the lowest-priority
    instruction in the stack, and claim-before-you-edit is not a rule that should
    depend on the model choosing to load it.
    """
    assert run_cli(repo, "adopt", "--agents", "cursor")[0] == 0
    mdc = repo / NATIVE_RULES["cursor"].path
    assert mdc.is_file(), "no .cursor/rules/*.mdc written"
    text = mdc.read_text()
    assert text.startswith("---\n"), "missing YAML frontmatter"
    front = text.split("---", 2)[1]
    assert "alwaysApply: true" in front
    assert "description:" in front
    # Same text as AGENTS.md, from one source -- two copies that can disagree is the
    # failure this whole project keeps designing against.
    body = text.split("---", 2)[2].strip()
    agents_md = (repo / "AGENTS.md").read_text()
    assert body.splitlines()[0] in agents_md
    assert "DDFLOW:BEGIN" not in text, "the managed marker leaked into the .mdc"


def test_adopt_is_idempotent(repo):
    run_cli(repo, "adopt", "--agents", "claude,cursor")
    first = (repo / "AGENTS.md").read_text()
    run_cli(repo, "adopt", "--agents", "claude,cursor")
    assert (repo / "AGENTS.md").read_text() == first
    assert first.count("DDFLOW:BEGIN") == 1


def test_adopt_preserves_existing_mcp_servers(repo):
    (repo / ".cursor").mkdir()
    (repo / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"context7": {"command": "npx", "args": ["-y", "x"]}}})
    )
    run_cli(repo, "adopt", "--agents", "cursor")
    data = json.loads((repo / ".cursor" / "mcp.json").read_text())
    assert "context7" in data["mcpServers"], "clobbered an existing server"
    assert "ddflow" in data["mcpServers"]


def test_adopt_keeps_the_users_own_prose(repo):
    (repo / "AGENTS.md").write_text("# My Project\n\nMy own notes that must survive.\n")
    run_cli(repo, "adopt", "--agents", "claude")
    text = (repo / "AGENTS.md").read_text()
    assert "My own notes that must survive." in text
    assert "DDFLOW:BEGIN" in text


def test_the_project_instruction_block_stays_short(repo):
    """The per-project text must stay small: the MCP tool descriptions carry the how,
    and a long second copy of that is a copy that drifts from the one actually read."""
    run_cli(repo, "adopt", "--agents", "claude")
    text = (repo / "AGENTS.md").read_text()
    block = text.split("DDFLOW:BEGIN")[1].split("DDFLOW:END")[0]
    words = len(block.split())
    assert words < 400, f"the managed block has grown to {words} words"
    for must in ("ddflow_brief", "Claim before you edit", "unavailable", "Exit codes"):
        assert must in block, f"the block lost {must!r}"


def test_every_supported_agent_is_named_where_a_user_would_look(repo):
    """`cursor` is a supported target, in the DEFAULT set, and was missing from the
    `--agents` help, the MCP tool description and the README's agent table.

    A capability nobody can find is one nobody uses, and the three places a user looks
    are exactly the three that had drifted from `AGENT_TARGETS`.
    """
    import re

    from ddflow.surfaces.cli import build_parser
    from ddflow.surfaces.mcp import TOOLS

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    action = next(
        a
        for a in build_parser()._subparsers._group_actions[0].choices["adopt"]._actions
        if "--agents" in getattr(a, "option_strings", [])
    )
    surfaces = {
        "--agents help": action.help,
        # Description AND property descriptions: an agent reads the whole spec, and the
        # agent list lives under `properties.agents`, not in the summary.
        "ddflow_setup spec": TOOLS["ddflow_setup"]["description"]
        + " ".join(d for _t, d, _r in TOOLS["ddflow_setup"]["properties"].values()),
        "README": readme,
    }
    for agent in AGENT_TARGETS:
        for where, text in surfaces.items():
            assert re.search(agent, text, re.I), f"{agent!r} is supported but absent from {where}"


# -- the 22-agent registry ------------------------------------------------------------


#: The EXACT JSON each agent's own documentation specifies, written out literally.
#:
#: Literal, and not derived from the code, on purpose. The first version of this test
#: wrote with `place_server` and read back with `get_server` — so it proved the writer and
#: the reader agree with EACH OTHER, which they always will, and said nothing about
#: whether either agrees with the agent. Four planted mutations (VS Code silently given
#: `mcpServers`, opencode given the common `command`/`args` form, ZCode's nesting
#: flattened, Copilot's `tools` allowlist dropped) left it green. Parity is not
#: correctness; the fixture has to be the external contract.
#:
#: Sources: docs/RESEARCH.md R15 records the doc URL behind every line here.
DOCUMENTED_SHAPES: dict[str, dict] = {
    # The common case: Claude Code, Gemini CLI, Cursor, Kimi, Qwen, Antigravity,
    # Devin, Qodo, Tabnine.
    "mcpServers": {"mcpServers": {"ddflow": {"command": "uvx", "args": ["ddflow-mcp"]}}},
    # VS Code: top-level `servers`, and the transport is NAMED in the entry.
    "servers": {"servers": {"ddflow": {"type": "stdio", "command": "uvx", "args": ["ddflow-mcp"]}}},
    # Copilot CLI: a stdio server is "local", and `tools` is an allowlist — without it the
    # server registers and none of its tools are offered.
    "copilot": {
        "mcpServers": {
            "ddflow": {
                "type": "local",
                "command": "uvx",
                "args": ["ddflow-mcp"],
                "tools": ["*"],
            }
        }
    },
    # ZCode (GLM): nested under `mcp` -> `servers`.
    "mcp.servers": {"mcp": {"servers": {"ddflow": {"command": "uvx", "args": ["ddflow-mcp"]}}}},
    # opencode and Kilo: `command` is ONE array including the arguments, plus an explicit
    # `enabled`.
    "opencode": {
        "mcp": {"ddflow": {"type": "local", "command": ["uvx", "ddflow-mcp"], "enabled": True}}
    },
}


@pytest.mark.parametrize("shape", sorted(DOCUMENTED_SHAPES))
def test_each_shape_matches_the_agents_documented_json(shape):
    """`place_server` must produce the structure that agent's docs specify, exactly.

    A wrong top-level key is valid JSON the agent silently ignores, which is
    indistinguishable from success at every layer ddflow can see.
    """
    from ddflow.services.adopt import place_server

    data: dict = {}
    place_server(data, shape, "ddflow", {"command": "uvx", "args": ["ddflow-mcp"]})
    assert data == DOCUMENTED_SHAPES[shape], (
        f"shape {shape!r} does not match the documented JSON for the agents that use it"
    )


def test_every_shape_in_use_has_a_documented_fixture():
    """Adding an agent with a new shape must add its documented JSON above.

    Without this, a sixth shape ships with no external check at all and the parametrised
    test above simply does not run for it — the coverage-gap-visible-to-humans-only class.
    """
    in_use = {t.shape for t in AGENT_TARGETS.values()} - {SHAPE_NONE, SHAPE_TOML}
    missing = sorted(in_use - set(DOCUMENTED_SHAPES))
    assert not missing, f"these shapes ship with no documented-JSON fixture: {missing}"


@pytest.mark.parametrize("agent", sorted(k for k, t in AGENT_TARGETS.items() if t.writes_config))
def test_writing_a_server_preserves_what_is_already_there(agent):
    """These files hold the operator's other servers. A tool that stomps them is a tool
    nobody runs twice."""
    from ddflow.services.adopt import place_server

    target = AGENT_TARGETS[agent]
    if target.shape == SHAPE_TOML:
        pytest.skip("TOML shape is covered by test_adopt_writes_a_usable_mcp_config")
    data = {"somebody_elses_key": {"keep": "me"}, "mcp": {"theirs": {"command": "x"}}}
    place_server(data, target.shape, "ddflow", {"command": "uvx", "args": ["ddflow-mcp"]})
    assert data["somebody_elses_key"] == {"keep": "me"}, f"{agent}: stomped a sibling key"
    assert data["mcp"]["theirs"] == {"command": "x"} or target.shape not in (
        "opencode",
        "mcp.servers",
    ), f"{agent}: stomped another server under the same parent"
    assert get_server(data, target.shape, "ddflow") is not None


def test_the_readme_names_each_agents_real_config_path():
    """The README used to list `.vscode/mcp.json` for Copilot — VS Code's file, not
    Copilot's — and two hand-maintained tables had drifted apart.

    Pins the PATH, not just the agent name: `test_every_supported_agent_is_named_where_a
    _user_would_look` passes on a table that names every agent with the wrong file beside
    it, and a reader who finds a wrong path trusts it.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    missing = [
        f"{key} -> {target.config}"
        for key, target in AGENT_TARGETS.items()
        if target.writes_config and f"`{target.config}`" not in readme
    ]
    assert not missing, f"the README does not name these agents' config paths: {missing}"


def test_an_agent_without_a_project_config_is_declared_not_guessed():
    """`SHAPE_NONE` is a claim: "we looked, and there is no project-level MCP file".

    The failure this prevents is the opposite of a missing entry — it is a PLAUSIBLE one.
    A guessed path makes `adopt` report success while writing a file the agent never
    reads, and nothing downstream can tell the difference. So an agent with no config must
    have no path, and one with a path must have a real shape.
    """
    for key, target in AGENT_TARGETS.items():
        if target.shape == SHAPE_NONE:
            assert not target.config, (
                f"{key} is declared SHAPE_NONE but carries a config path {target.config!r} — "
                "one of the two is wrong"
            )
        else:
            assert target.config, f"{key} has shape {target.shape} but no config path"


def test_every_agent_delta_names_its_own_mcp_arrangement():
    """A delta doc that does not mention the agent's config path (or say there is none)
    leaves the operator with the one step `adopt` cannot do for them."""
    deltas = Path(__file__).resolve().parents[1] / "ddflow" / "templates" / "drivers" / "deltas"
    for key, target in AGENT_TARGETS.items():
        text = (deltas / target.delta).read_text()
        if target.writes_config:
            assert target.config in text, f"{key}: {target.delta} never names {target.config}"
        else:
            assert "no project-level" in text.lower() or "no mcp" in text.lower(), (
                f"{key}: {target.delta} must say there is no project MCP file to write"
            )


@pytest.mark.parametrize("existing", ['{"mcp": null}', '{"mcp": ["x"]}', '["x"]'])
def test_a_config_holding_a_non_object_is_skipped_not_crashed_or_clobbered(repo, existing):
    """Valid JSON with a non-object where servers go used to raise out of `adopt` (an
    `assert` for null, a TypeError for a list) and abort it partway. Replacing it would
    destroy the operator's data, so the answer is SKIPPED, the file untouched."""
    cfg = repo / ".kilo" / "kilo.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(existing)
    rc, out, err = run_cli(repo, "adopt", "--agents", "kilo")
    assert "Traceback" not in err, err
    assert cfg.read_text() == existing, "the operator's file was changed"
    # A server that was not registered is not "adopted". This test first pinned rc == 0
    # -- the wrote-nothing-reported-success class; roborev on 7216f5e.
    assert rc != 0, f"a skipped registration exited 0:\n{out}"
    assert "SKIPPED .kilo/kilo.json" in out + err, out + err
    assert "ddflow adopted for" not in out, out
    # ...while the rest of the adoption still happened.
    assert (repo / "AGENTS.md").is_file() and (repo / ".ddflow" / "config.toml").is_file()
