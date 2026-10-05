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


@pytest.mark.parametrize("key", ['"gate".a.b.command', '"gate"'])
def test_no_unusable_plain_spelling_is_suggested(repo, key):
    """roborev on 04ffcb6e and babe29ff: the suggested spelling was itself refused -- a
    dotted gate id (`gate.a.b.command`) or a single segment (`gate`)."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", key, "x")
    assert code == REFUSED and "; use " not in err, (code, out, err)
