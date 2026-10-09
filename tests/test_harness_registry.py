"""The harness registry (services/harnessreg) and the tables that are views over it.

The parity tests pin adopt.AGENT_TARGETS, NATIVE_RULES and AGENT_COMMANDS to the values the
hand-written tables held before the descriptors existed (order included: listings and the
adopt output follow it), so moving the facts into ddflow/harnesses/*.toml changed nothing a
user can see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.services import adopt, harnessreg

#: (agent, delta, config, shape) exactly as AGENT_TARGETS held them before the descriptors.
_TARGETS = [
    ("claude", "claude-code.md", ".mcp.json", "mcpServers"),
    ("gemini", "gemini-cli.md", ".gemini/settings.json", "mcpServers"),
    ("codex", "codex-cli.md", ".codex/config.toml", "toml.mcp_servers"),
    ("copilot", "github-copilot.md", ".github/mcp.json", "copilot"),
    ("vscode", "vscode.md", ".vscode/mcp.json", "servers"),
    ("kilo", "kilo-cline.md", ".kilo/kilo.json", "opencode"),
    ("cursor", "cursor.md", ".cursor/mcp.json", "mcpServers"),
    ("kimi", "kimi-code.md", ".kimi-code/mcp.json", "mcpServers"),
    ("opencode", "opencode.md", "opencode.json", "opencode"),
    ("glm", "zcode-glm.md", ".zcode/config.json", "mcp.servers"),
    ("qwen", "qwen-code.md", ".qwen/settings.json", "mcpServers"),
    ("antigravity", "antigravity.md", ".agents/mcp_config.json", "mcpServers"),
    ("devin", "devin.md", ".devin/mcp_config.json", "mcpServers"),
    ("qodo", "qodo.md", "mcp.json", "mcpServers"),
    ("tabnine", "tabnine.md", ".tabnine/agent/settings.json", "mcpServers"),
    ("aider", "aider.md", "", "none"),
    ("cline", "cline.md", "", "none"),
    ("windsurf", "windsurf.md", "", "none"),
    ("replit", "replit.md", "", "none"),
    ("openhands", "openhands.md", "", "none"),
    ("goose", "goose.md", "", "none"),
    ("cody", "cody.md", "", "none"),
]

#: (agent, path, form, why) exactly as NATIVE_RULES held them, in order.
_NATIVE = [
    (
        "cursor",
        ".cursor/rules/ddflow.mdc",
        "whole",
        "Cursor ranks project rules ABOVE AGENTS.md, so AGENTS.md alone is outranked",
    ),
    ("qwen", "QWEN.md", "block", "QWEN.md is Qwen Code's DEFAULT context file, not AGENTS.md"),
    ("cline", ".clinerules/ddflow.md", "block", "Cline reads .clinerules/, not AGENTS.md"),
    (
        "tabnine",
        ".tabnine/guidelines/ddflow.md",
        "block",
        "Tabnine Agent reads .tabnine/guidelines/*.md, not AGENTS.md",
    ),
    ("replit", "replit.md", "block", "Replit reads replit.md at the project root, not AGENTS.md"),
    ("goose", ".goosehints", "block", "Goose reads .goosehints as well, and it is committed"),
    (
        "aider",
        ".aider.conf.yml",
        "aider-conf",
        "Aider loads only what `read:` names — it discovers no instruction file at all",
    ),
]

_COMMANDS = {"claude": {".claude/commands/implement.md": "commands/claude/implement.md"}}

_GOOD = """
id = "demo"
rank = 99
family = "demo"
delta = "demo.md"

[mcp]
path = ".demo/mcp.json"
shape = "mcpServers"

[verified]
date = "2026-10-09"
sources = ["https://example.test/docs"]
"""


def test_agent_targets_match_the_tables_they_replaced():
    got = [(k, t.delta, t.config, t.shape) for k, t in adopt.AGENT_TARGETS.items()]
    assert got == _TARGETS


def test_native_rules_match_the_table_they_replaced():
    got = [(k, r.path, r.form, r.why) for k, r in adopt.NATIVE_RULES.items()]
    assert got == _NATIVE


def test_agent_commands_match_the_table_they_replaced():
    assert list(adopt.AGENT_COMMANDS.items()) == list(_COMMANDS.items())


def test_every_descriptor_loads_and_has_its_delta_doc():
    deltas = Path(adopt.__file__).resolve().parent.parent / "templates" / "drivers" / "deltas"
    for h in harnessreg.load():
        assert (deltas / h.delta).is_file(), f"{h.id}: delta {h.delta} is missing"
        assert h.verified.sources, h.id


def test_get_and_ids():
    claude = harnessreg.get("claude")
    assert claude is not None and claude.mcp.path == ".mcp.json"
    assert harnessreg.get("nonesuch") is None
    assert harnessreg.ids()[0] == "claude"


def test_unverified_is_not_the_same_as_none():
    """A missing section is NOT VERIFIED (None); `none = true` is a verified absence."""
    aider = harnessreg.get("aider")
    assert aider is not None and aider.plugin is not None and aider.plugin.none is True
    glm = harnessreg.get("glm")
    assert glm is not None
    assert glm.hooks is None and glm.plugin is None and glm.skills is None
    assert "everything" in glm.verified.unverified[0]


def test_hook_events_are_canonical_and_injection_is_mapped():
    for h in harnessreg.load():
        if h.hooks is None:
            continue
        assert set(h.hooks.events) <= set(harnessreg.CANONICAL_EVENTS), h.id
        assert set(h.hooks.inject) <= set(h.hooks.events), h.id


def test_hook_facts_from_the_research_record():
    claude = harnessreg.get("claude")
    assert claude is not None and claude.hooks is not None and claude.env is not None
    assert claude.hooks.events["prompt"] == "UserPromptSubmit"
    assert claude.hooks.session_id == "session_id"
    assert claude.env.session_id == ("CLAUDE_CODE_SESSION_ID",)
    # Copilot CLI drops a prompt hook's output: the case the fallback ladder exists for.
    copilot = harnessreg.get("copilot")
    assert copilot is not None and copilot.hooks is not None
    assert "prompt" not in copilot.hooks.inject


def test_parse_accepts_a_minimal_descriptor():
    h = harnessreg.parse(_GOOD, "demo")
    assert h.mcp.shape == "mcpServers" and h.hooks is None and h.instructions is None


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace('id = "demo"', 'id = "other"'), "must equal the file name"),
        (lambda t: "surprise = 1\n" + t, "unknown top-level key"),
        (lambda t: t.replace('shape = "mcpServers"', 'shape = "bogus"'), "is not one of"),
        (lambda t: t.replace('path = ".demo/mcp.json"', 'path = ""'), "empty exactly when"),
        (
            lambda t: t.replace('sources = ["https://example.test/docs"]', "sources = []"),
            "one source",
        ),
        (lambda t: t.replace("rank = 99", 'rank = "99"'), "expected int"),
        (lambda t: t.replace("[mcp]", '[mcp]\ncolour = "red"'), "unknown key"),
        (lambda t: t + '\n[plugin]\nnone = true\nmanifest = "x"\n', "none = true and also"),
        (lambda t: t + "\n[skills]\n", "is empty: omit it"),
        (
            lambda t: t.replace('[mcp]\npath = ".demo/mcp.json"\nshape = "mcpServers"', ""),
            "expected a table",
        ),
        (lambda t: t + '\n[hooks]\nstyle = "claude"\n', "needs file, normalizer, emitter"),
        (lambda t: t + '\n[hooks]\nstyle = "none"\nfile = "x"\n', "takes no file"),
        (
            lambda t: t + '\n[hooks]\nstyle = "plugin"\n[hooks.events]\nsparkle = "x"\n',
            "non-canonical",
        ),
        (lambda t: t + '\n[hooks]\nstyle = "plugin"\ninject = ["prompt"]\n', "no mapping"),
        (lambda t: t + '\n[instructions]\nnative_path = "X.md"\n', "go together"),
        (lambda t: t + '\n[instructions]\nagents_md = "maybe"\n', "agents_md must be one of"),
        (lambda t: t + "\n[mcp", "demo.toml"),
    ],
)
def test_malformed_descriptors_are_refused_with_the_file_named(edit, message):
    with pytest.raises(harnessreg.DescriptorError, match=message):
        harnessreg.parse(edit(_GOOD), "demo")


def test_duplicate_rank_is_refused(tmp_path):
    for name in ("a", "b"):
        text = _GOOD.replace('id = "demo"', f'id = "{name}"')
        (tmp_path / f"{name}.toml").write_text(text)
    with pytest.raises(harnessreg.DescriptorError, match="rank 99"):
        harnessreg.load(tmp_path)


def test_load_orders_by_rank(tmp_path):
    for name, rank in (("a", 2), ("b", 1)):
        text = _GOOD.replace('id = "demo"', f'id = "{name}"').replace("rank = 99", f"rank = {rank}")
        (tmp_path / f"{name}.toml").write_text(text)
    assert [h.id for h in harnessreg.load(tmp_path)] == ["b", "a"]
