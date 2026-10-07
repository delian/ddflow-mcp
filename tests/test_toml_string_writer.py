"""Bb11e7a8186: the TOML string writers quoted with `json.dumps` (ensure_ascii), which
writes a character outside the BMP -- an emoji -- as a UTF-16 surrogate pair
`\\ud83d\\ude80`. A TOML `\\u` escape must be a Unicode scalar value, so tomllib refused
the file: `ddflow config <key> "ship it 🚀"` wrote a config no later command could read.
Every writer must round-trip any text through tomllib."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra.tomlcfg import value as _toml
from ddflow.services.configwrite import _toml_literal
from ddflow.surfaces.commands.review import _toml_value

TEXTS = ["ship it 🚀", "del\x7fete", 'quote " back\\slash', "tab\tnew\nline", "é ü 中文 𝄞"]


@pytest.mark.parametrize("writer", [_toml_literal, _toml_value, _toml], ids=lambda f: f.__name__)
@pytest.mark.parametrize("text", TEXTS)
def test_every_toml_string_writer_round_trips_any_text(writer, text):
    assert tomllib.loads(f"x = {writer(text)}")["x"] == text


def test_nested_values_round_trip_an_emoji():
    value = {"env": {"NOTE": "🚀"}, "args": ["a", "🎉"]}
    for writer in (_toml_value, _toml):
        assert tomllib.loads(f"x = {writer(value)}")["x"] == value


def test_config_set_with_an_emoji_leaves_a_readable_config(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "flow.tag_prefix", "🚀v")[0] == 0
    data = tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))
    assert data["flow"]["tag_prefix"] == "🚀v"
    code, out = run_cli(repo, "config", "--filter", "tag_prefix")[:2]
    assert code == 0 and "🚀v" in out, out


def test_frozen_manifest_round_trips_a_path_with_an_emoji(tmp_path):
    from ddflow.services import legacy as L

    (tmp_path / ".ddflow").mkdir()
    (tmp_path / "notes 🚀.md").write_text("x\n", "utf-8")
    L.write_manifest(tmp_path, ["notes 🚀.md"])
    assert L.read_frozen(tmp_path) == {"notes 🚀.md": L.sha256_file(tmp_path / "notes 🚀.md")}


def test_workflow_pipeline_with_an_emoji_gate_id_writes_a_readable_pipeline(repo):
    """roborev on 477845b7: api/workflow serialised pipelines with json.dumps, and
    `_toml_literal` passes an array through untouched."""
    from conftest import run_cli

    run_cli(repo, "init")
    # Hand-written: no ddflow surface writes a quoted key (D-plain-keys), but a file
    # that has one is still read as TOML.
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text("utf-8") + '\n[gate."ship🚀"]\nprompt = "sign it"\n', "utf-8")
    code, out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,ship🚀,merge")[:3]
    assert code == 0, (out, err)
    data = tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))
    assert data["gates"]["task_pipeline"] == ["implement", "ship🚀", "merge"]
    assert run_cli(repo, "workflow", "drop", "ship🚀")[0] == 0
    data = tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))
    assert data["gates"]["task_pipeline"] == ["implement", "merge"]


def test_a_lone_surrogate_is_refused_with_a_plain_error_not_written():
    """rubber_duck on 3bb01ef7: an undecodable filename byte arrives as a lone surrogate
    (PEP 383). TOML cannot hold one; writing it raw crashed the UTF-8 encode later."""
    from ddflow.infra.tomlcfg import basic_string

    with pytest.raises(ValueError, match="U\\+DCE9"):
        basic_string("caf\udce9.txt")


def test_reviewers_add_quotes_the_name(repo):
    """roborev on 3bb01ef7: `name = "{name}"` was interpolated raw."""
    from conftest import run_cli

    run_cli(repo, "init")
    name = 'my "r" 🚀\\'
    code, out, err = run_cli(repo, "reviewers", "add", "--preset", "openai", "--name", name)[:3]
    assert code == 0, (out, err)
    path = repo / ".ddflow" / "local" / "reviewers.toml"
    assert [r["name"] for r in tomllib.loads(path.read_text("utf-8"))["reviewer"]] == [name]


def test_reviewers_detect_quotes_what_the_endpoint_reported(repo, monkeypatch):
    from conftest import run_cli

    from ddflow.api.review import reviewers_detect
    from ddflow.services import review as R

    run_cli(repo, "init")
    model = 'org/odd "model" 🚀'
    monkeypatch.setattr(R, "detect", lambda *a, **k: [("http://127.0.0.1:9/v1", "x", [model])])
    out = reviewers_detect(repo, write=True)
    path = repo / ".ddflow" / "local" / "reviewers.toml"
    assert path.is_file(), out
    assert tomllib.loads(path.read_text("utf-8"))["reviewer"][0]["model"] == model


def test_adopt_codex_writes_a_launch_entry_that_parses(repo, monkeypatch):
    """adopt wrote `command = "{...}"` and the args raw into the agent's own config.toml,
    unguarded: a quote or a backslash in the path broke every MCP server in the file."""
    from ddflow.services import adopt as A

    entry = {
        "command": 'C:\\tools\\uv "x".exe',
        "args": ["--dir", "D:\\repo 🚀"],
        "env": {"PYTHONPATH": '/src/dd "flow"'},
    }
    monkeypatch.setattr(A, "_launch_entry", lambda *a, **k: dict(entry))
    A._register_mcp(repo, "codex")
    data = tomllib.loads((repo / A.AGENT_TARGETS["codex"].config).read_text("utf-8"))
    assert data["mcp_servers"]["ddflow"] == entry
