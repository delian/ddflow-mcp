"""B57fc667efc: MCP prompts/get said only "unknown prompt" when the [[macro]] config
could not be read at all; doctor and the CLI named the cause."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.surfaces.mcp import Server


def _get(repo, name):
    return Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": name}}
    )


@pytest.mark.parametrize(
    "config, why",
    [
        ('\n[[macro]]\nname = "x"\nprompt = "p"\nbogus = 1\n', "unknown field"),
        ('\n[[macro]]\nname = "x"\nprompt = \n', "Invalid value"),
    ],
)
def test_prompts_get_names_why_the_macro_config_could_not_be_loaded(repo, config, why, monkeypatch):
    import ddflow.config as C

    # An unknown field is an error in the code tree only (B0016a65167).
    monkeypatch.setattr(C, "_CODE_TREE", repo.resolve())
    (repo / ".ddflow").mkdir(exist_ok=True)
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write(config)
    got = _get(repo, "x")
    msg = got["error"]["message"]
    assert "unknown prompt" in msg and "not loaded" in msg and why in msg, msg


def test_a_healthy_config_adds_no_note(repo):
    msg = _get(repo, "nope")["error"]["message"]
    assert "unknown prompt" in msg and "not loaded" not in msg, msg
