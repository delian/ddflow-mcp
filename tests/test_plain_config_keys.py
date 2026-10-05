"""D-plain-keys: every key a CLI or MCP surface accepts is a dot-separated list of TOML
bare-key segments (ASCII letters, digits, `_`, `-`). A quoted segment, a string escape or
non-ASCII is REFUSED (exit 3) with the plain spelling named when one exists -- no user is
ever told to type an escape. Hand-written TOML files are still read as TOML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

REFUSED = 3


@pytest.mark.parametrize(
    ("key", "plain"),
    [
        ('gate."unit_tests".command', "gate.unit_tests.command"),
        ("gate.'unit_tests'.command", "gate.unit_tests.command"),
        ('gate.unit_tests."\\u0063ommand"', "gate.unit_tests.command"),
        ('"flow".tag_prefix', "flow.tag_prefix"),
        (" flow . tag_prefix", "flow.tag_prefix"),
    ],
)
def test_a_quoted_or_escaped_key_is_refused_naming_the_plain_one(repo, key, plain):
    run_cli(repo, "init")
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, out, err = run_cli(repo, "config", "--set", key, "x")
    assert code == REFUSED, (code, out, err)
    assert f"use {plain}" in err, err
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before


@pytest.mark.parametrize("key", ["gate.🚀.prompt", "gate.two words.command", "flow.tag\\x"])
def test_a_key_with_no_plain_spelling_is_refused(repo, key):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", key, "x")
    assert code == REFUSED, (code, out, err)
    assert "letters, digits" in err, err


def test_the_mcp_configure_tool_refuses_it_too(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_configure",
                "arguments": {"set": 'gate."unit_tests".command', "value": "x"},
            },
        }
    )
    text = r["result"]["content"][0]["text"]
    assert "use gate.unit_tests.command" in text, text
    assert r["result"]["_meta"]["exit"] == REFUSED, json.dumps(r)


def test_a_plain_key_is_still_written(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")
    assert code == 0, (out, err)


def test_a_hand_written_quoted_key_is_still_read(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text("utf-8") + '\n[gate."unit_tests"]\ntimeout_s = 77\n', "utf-8")
    assert run_cli(repo, "brief")[0] in (0, 2)


def test_a_quoted_human_key_is_refused_as_not_plain_exit_3(repo):
    """roborev on 04ffcb6e: the human-flag guard ran first and answered exit 1; the exit
    code says 'not a plain key' whatever field the key names."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", 'gate.unit_tests."human"', "false")
    assert code == REFUSED and "refusing" in err, (code, out, err)


@pytest.mark.parametrize("key", ['"gate".a.b.command', '"gate"', 'gate.unit_tests."human"'])
def test_no_unusable_plain_spelling_is_suggested(repo, key):
    """roborev on 04ffcb6e and babe29ff: the suggested spelling was itself refused -- a
    dotted gate id (`gate.a.b.command`) or a single segment (`gate`)."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", key, "x")
    assert code == REFUSED and "; use " not in err, (code, out, err)


# B7a1ed66cb9: the surfaces fix-B72b8adba30 did not reach -- raw TOML appended through
# `config --append-toml` / `ddflow_configure toml`, companion ids, and the workflow_drop
# description.


@pytest.mark.parametrize(
    ("block", "plain"),
    [
        ('[flow]\n"tag_prefix" = "v"\n', "flow.tag_prefix"),
        ('[gate."unit_tests"]\ntimeout_s = 5\n', "gate.unit_tests"),
        ('[[ "reviewer" . x ]]\nname = "r"\n', "reviewer.x"),
        # A single-segment header has a plain spelling too (rubber_duck/critic on a1c614f4).
        ('["flow"]\ntag_prefix = "v"\n', "[flow]"),
        ("[['reviewer']]\nname = 'r'\n", "[[reviewer]]"),
        ('[flow]\n"\\u0074ag_prefix" = "v"\n', "flow.tag_prefix"),
        ("flow.'tag_prefix' = 'v'\n", "flow.tag_prefix"),
        ("flow . tag_prefix = 'v'\n", "flow.tag_prefix"),
        ('[gate.unit_tests]\nenv = {"FOO" = "1"}\n', "gate.unit_tests.env.FOO"),
    ],
)
def test_an_appended_block_with_a_quoted_key_is_refused_naming_the_plain_one(repo, block, plain):
    run_cli(repo, "init")
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, out, err = run_cli(repo, "config", "--append-toml", block)
    assert code == REFUSED, (code, out, err)
    assert f"use {plain}" in err, err
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before


@pytest.mark.parametrize("block", ['[gate."two words"]\nprompt = "x"\n', '["two words"]\nx = 1\n'])
def test_an_appended_key_with_no_plain_spelling_is_refused(repo, block):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--append-toml", block)
    assert code == REFUSED and "letters, digits" in err, (code, out, err)


def test_quotes_in_values_and_comments_are_not_keys(repo):
    run_cli(repo, "init")
    block = (
        '[flow]  # "not" = "a key"\n'
        'tag_prefix = "v\\"x = 1"\n'
        "[gate.unit_tests]\n"
        'env = {FOO = \'a = b\', BAR = """\nx"y"""}\n'
        'prompt = """\n"k" = 1\n[not.a."header"]\n"""\n'
    )
    code, out, err = run_cli(repo, "config", "--append-toml", block)
    assert code == 0, (code, out, err)


def test_the_mcp_configure_tool_refuses_a_quoted_key_in_toml(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_configure",
                "arguments": {"toml": '[flow]\n"tag_prefix" = "v"\n'},
            },
        }
    )
    text = r["result"]["content"][0]["text"]
    assert "use flow.tag_prefix" in text, text
    assert r["result"]["_meta"]["exit"] == REFUSED, json.dumps(r)


@pytest.mark.parametrize("cid", ["a.b", "two words"])
def test_a_companion_id_that_is_not_a_bare_key_is_refused(repo, cid):
    """A dotted id wrote `[mcp_servers.a.b]` into codex's config: a nested table, not a
    server named `a.b`."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        f'[[companion]]\nid = "{cid}"\ncommand = "echo"\n', "utf-8"
    )
    code, out, err = run_cli(repo, "companions", "add", "--id", cid, "--agents", "codex", "--force")
    assert code == REFUSED and "letters, digits" in out + err, (code, out, err)
    assert not (repo / ".codex" / "config.toml").exists()


def test_workflow_drop_describes_every_pipeline():
    from ddflow.surfaces.mcp import TOOLS

    desc = TOOLS["ddflow_workflow_drop"]["description"]
    assert "both pipelines" not in desc and "promotion" in desc, desc


@pytest.mark.parametrize(
    "block", ['[gate.a.b]\ncommand = "echo hi"\n', '[gate.x.prompt]\ncommand = "y"\n']
)
def test_an_appended_dotted_gate_id_is_refused(repo, block):
    """roborev on a1c614f4: every segment of `[gate.a.b]` is bare, so the plain-key check
    passed it -- and it nested a table that every later command refused to load."""
    run_cli(repo, "init")
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, out, err = run_cli(repo, "config", "--append-toml", block)
    assert code == REFUSED and "cannot be a gate id" in err, (code, out, err)
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before


def test_every_new_gate_problem_in_an_appended_block_is_named(repo):
    """roborev on 896b024d: only the first was reported."""
    run_cli(repo, "init")
    block = '[gate.a.b]\ncommand = "x"\n[gate.c.d]\ncommand = "y"\n'
    code, out, err = run_cli(repo, "config", "--append-toml", block)
    assert code == REFUSED and "'a.b'" in err and "'c.d'" in err, (code, out, err)
