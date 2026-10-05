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

from ddflow.services.companions import _toml
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
