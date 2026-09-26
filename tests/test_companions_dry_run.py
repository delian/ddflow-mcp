"""Registering a server changes which processes an agent will launch. Show it first.

The handshake has always told the agent to ask the operator before wiring a companion
in. Until `dry_run` existed that instruction had nothing behind it: the agent could
describe the change in its own words, or make it and report afterwards. Neither is the
operator seeing what will be written.

This is the same principle as the human-approval gate one file over, applied at a lower
stake. There it is enforced — no MCP tool exists. Here the operation is small,
reversible, repo-local and merged rather than overwritten, so the agent keeps it and
gains a way to make "ask first" actionable instead of aspirational.

*Operator's call, asked and answered on 2026-09-25: add `dry_run` rather than removing
the tool from the MCP surface.*
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

REGISTRY = (
    "[[companion]]\n"
    'id = "fake"\n'
    'title = "Fake server"\n'
    'command = "true"\n'
    'args = ["--serve"]\n'
    'detect = ["true"]\n'
    'install = "brew install fake"\n'
    'url = "https://example.com"\n'
    "default = true\n"
)


def _setup(repo) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(REGISTRY)


def test_a_dry_run_writes_nothing_at_all(repo):
    """Not the file, and not the directory either. A dry run that still creates a
    parent directory is a dry run that changed the machine."""
    _setup(repo)
    before = sorted(p.name for p in repo.iterdir())
    code, out, err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    assert code == OK, err
    assert not (repo / ".mcp.json").exists(), "the dry run wrote the config"
    assert sorted(p.name for p in repo.iterdir()) == before, "the dry run created something"
    assert "WOULD add" in out


def test_the_dry_run_shows_the_ACTUAL_entry_not_a_description(repo):
    """The point is that the operator sees what will be written. A summary in the
    agent's own words is exactly what this replaces."""
    _setup(repo)
    _code, out, _err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    body = out.split("WOULD add to .mcp.json:", 1)[1]
    parsed = json.loads(body[body.index("{") :])
    assert parsed == {"mcpServers": {"fake": {"command": "true", "args": ["--serve"]}}}, parsed


def test_the_dry_run_output_matches_what_the_real_write_produces(repo):
    """Otherwise the operator approves one thing and gets another — which is worse than
    not showing them anything, because they have now signed off on it."""
    _setup(repo)
    _code, dry, _err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    shown = json.loads(dry[dry.index("{") :])

    run_cli(repo, "companions", "add", "--id", "fake")
    written = json.loads((repo / ".mcp.json").read_text())
    assert written["mcpServers"]["fake"] == shown["mcpServers"]["fake"]


def test_a_real_add_still_writes(repo):
    """The guard against a `dry_run` default that quietly disables the command."""
    _setup(repo)
    code, _out, err = run_cli(repo, "companions", "add", "--id", "fake")
    assert code == OK, err
    assert (repo / ".mcp.json").exists()


def test_a_dry_run_says_so_when_nothing_would_change(repo):
    """Re-running it must not show a config entry that would be a no-op — "here is what
    I would write" over an already-registered server invites the operator to approve
    a change that is not one."""
    _setup(repo)
    run_cli(repo, "companions", "add", "--id", "fake")
    _code, out, _err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    assert "already registers" in out, out
    assert "WOULD add" not in out, out


def test_the_dry_run_preserves_an_existing_config_in_the_preview(repo):
    """The real write MERGES, so the preview must show the merged result.

    The first version of this test never ran a dry run at all — it wrote a config, did
    a REAL `companions add`, and asserted the write merged. Its docstring described the
    misleading-preview property while the code checked the write, so the one test whose
    name covered that case passed regardless of what the preview said. Found by roborev
    on 137f362.
    """
    _setup(repo)
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"mine": {"command": "keep-me"}}}, indent=2)
    )
    _code, out, _err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    shown = json.loads(out[out.index("{") :])
    assert "mine" in shown["mcpServers"], (
        f"the preview shows a lone entry while the write would merge:\n{out}"
    )
    assert "fake" in shown["mcpServers"]

    run_cli(repo, "companions", "add", "--id", "fake")
    written = json.loads((repo / ".mcp.json").read_text())
    assert written["mcpServers"] == shown["mcpServers"], (
        "the operator approved one thing and got another"
    )


def test_a_preview_never_promises_a_write_that_would_be_declined(repo):
    """An unparseable config makes the real write REFUSE. The preview used to report
    "WOULD add" over it — signing the operator off on a change that cannot happen,
    which is worse than showing them nothing."""
    _setup(repo)
    (repo / ".mcp.json").write_text("{ this is not json")
    _code, out, err = run_cli(repo, "companions", "add", "--id", "fake", "--dry-run")
    assert "WOULD add" not in (out + err), out + err
    assert "not valid JSON" in (out + err)


def test_the_preview_matches_the_write_for_a_TOML_target_too(repo):
    """Parametrised over the second branch. `codex` writes `.codex/config.toml`, whose
    preview was a copy-paste re-derivation and already differed from the write — no
    test exercised it, because every other case here registers for `claude`."""
    _setup(repo)
    _code, out, err = run_cli(
        repo, "companions", "add", "--id", "fake", "--agents", "codex", "--dry-run"
    )
    assert "config.toml" in out, out + err
    assert not (repo / ".codex" / "config.toml").exists(), "the dry run wrote the file"

    run_cli(repo, "companions", "add", "--id", "fake", "--agents", "codex")
    written = (repo / ".codex" / "config.toml").read_text()
    body = out[out.index("[mcp_servers") :].strip()
    assert body in written, f"preview differs from the write:\npreview={body!r}\nwrote={written!r}"


def test_the_mcp_tool_exposes_dry_run_and_says_to_use_it_first(repo):
    """A capability an agent cannot discover is one that does not exist, and a WRITE
    tool that does not say it writes is one an agent reaches for casually."""
    from ddflow.surfaces.mcp import TOOLS

    spec = TOOLS["ddflow_companions_add"]
    assert "dry_run" in spec["properties"]
    assert spec["argv"]({"id": "x", "dry_run": True})[-1] == "--dry-run"
    desc = spec["description"]
    assert "WRITES" in desc, "a config-writing tool must say so in its description"
    assert "dry_run=true FIRST" in desc
    assert "operator" in desc


def test_the_dry_run_reaches_the_tool_over_real_json_rpc(repo):
    """End to end, because the argv lambda passing the flag and the CLI honouring it
    are two different claims."""
    from ddflow.surfaces.mcp import Server

    _setup(repo)
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_companions_add",
                "arguments": {"id": "fake", "dry_run": True},
            },
        }
    )
    text = reply["result"]["content"][0]["text"]
    # `"applied"` is in every --json payload, so the old `or` clause could not fail.
    assert "WOULD add" in text, text
    assert not (repo / ".mcp.json").exists(), "a dry run over MCP wrote the config"


# -- what roborev found on the unification -------------------------------------------


def test_a_refusal_is_not_reported_as_success(repo):
    """`register` returned only a MESSAGE, so the caller could not tell a write from a
    refusal. An unparseable `.mcp.json` printed "SKIPPED … not valid JSON", reported
    `applied: true` and exited 0 — nothing written, surface saying otherwise. Bug class
    #1 in this repo's own review guidelines, in the command that writes config.

    *roborev on 0674a33, CONFIRMED.*
    """
    _setup(repo)
    (repo / ".mcp.json").write_text("{ not json")
    code, out, err = run_cli(repo, "companions", "add", "--id", "fake")
    assert code == FAIL, f"a refusal exited {code}"
    assert "not valid JSON" in (out + err)

    code, out, _err = run_cli(repo, "--json", "companions", "add", "--id", "fake")
    payload = json.loads(out)
    assert payload["applied"] is False, payload
    assert payload["refused"], payload


def test_re_registering_REFRESHES_a_changed_launch_command(repo):
    """The regression the unification introduced. The JSON branch used to assign the
    entry unconditionally, so re-running `companions add` refreshed a stale one. Hoisting
    the already-registered check turned that into a no-op: an entry whose command had
    changed in the registry stayed stale forever, and the obvious remedy reported success
    and did nothing.

    *roborev on 0674a33, CONFIRMED regression.*
    """
    _setup(repo)
    run_cli(repo, "companions", "add", "--id", "fake")
    assert json.loads((repo / ".mcp.json").read_text())["mcpServers"]["fake"]["args"] == ["--serve"]

    # the registry changes -- a new upstream launch command
    (repo / ".ddflow" / "companions.toml").write_text(
        REGISTRY.replace('args = ["--serve"]', 'args = ["--serve", "--v2"]')
    )
    code, _out, err = run_cli(repo, "companions", "add", "--id", "fake")
    assert code == OK, err
    got = json.loads((repo / ".mcp.json").read_text())["mcpServers"]["fake"]["args"]
    assert got == ["--serve", "--v2"], f"a stale entry was not refreshed: {got}"


def test_an_identical_entry_is_reported_as_unchanged_not_written(repo):
    """The other half: re-running with nothing to change must not claim a write."""
    _setup(repo)
    run_cli(repo, "companions", "add", "--id", "fake")
    code, out, _err = run_cli(repo, "--json", "companions", "add", "--id", "fake")
    assert code == OK
    payload = json.loads(out)
    assert payload["written"] == 0, payload
    assert payload["applied"] is False, payload
