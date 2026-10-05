"""B72b8adba30: `workflow gate` wrote `[gate.<id>]` with the id unquoted and unchecked, so an
id that is not a TOML bare key (a space, a dot, an emoji) was refused only as a raw TOML
parse error naming a config line. It is refused up front, saying what is wrong."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli


@pytest.mark.parametrize("gid", ["ship🚀", "two words", "a.b", "x]"])
def test_a_gate_id_that_is_not_a_bare_key_is_refused_plainly(repo, gid):
    run_cli(repo, "init")
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, out, err = run_cli(repo, "workflow", "gate", gid, "--prompt", "x", "--into", "task")
    assert code != 0
    text = out + err
    assert "letters, digits" in text and "TOML" not in text, text
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before


def test_an_ordinary_gate_id_is_still_accepted(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "workflow", "gate", "deploy_check-2", "--prompt", "x")
    assert code == 0, (out, err)


@pytest.mark.parametrize(
    "key",
    [
        "gate.a.b.command",
        "gate.two words.command",
        "gate.🚀.prompt",
        '"gate".a.b.command',
        "'gate'.a.b.command",
    ],
)
def test_config_set_refuses_a_gate_key_whose_id_is_not_a_bare_key(repo, key):
    """roborev on 6eafc1f3: `config --set gate.a.b.command` exited 0 and wrote
    `[gate.a.b]`, which every later command then refused to load."""
    run_cli(repo, "init")
    before = (repo / ".ddflow" / "config.toml").read_text("utf-8")
    code, out, err = run_cli(repo, "config", "--set", key, "echo hi")
    assert code != 0, (out, err)
    assert "letters, digits" in out + err, (out, err)
    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == before
    assert run_cli(repo, "brief")[0] in (0, 2)


def test_a_quoted_bare_gate_id_is_still_accepted(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", 'gate."unit_tests".command', "echo hi")
    assert code == 0, (out, err)
