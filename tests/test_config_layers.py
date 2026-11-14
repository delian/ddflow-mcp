"""A list knob in a lower config layer replaces the higher layer's list (D-config-layer-order):
the entries the lower layer dropped are NAMED at load and by `doctor`, never gone in
silence (B10fb90c1af, r-tighten-only: a layer may tighten, never silently loosen).

Before the fix a machine-local `[approvals].policy = ["accept_baseline_red"]` turned off the
committed file's standing rules with no note from config load or `doctor`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ddflow.config._warn as W
from ddflow.config import Config

#: The standing auto-approval rules `[approvals].policy` may enable in the committed layer;
#: a lower layer turning some off must be said out loud.
FILE_POLICY = [
    "skip_after_unavailable",
    "accept_baseline_red",
    "complete_unavailable",
    "claim_unavailable",
]


@pytest.fixture(autouse=True)
def _fresh_drop_memo(monkeypatch):
    """The warn-once memo is process-global; each test starts from an empty one."""
    monkeypatch.setattr(W, "_WARNED_DROPPED", set())


def _write_layers(root: Path, local: list[str]) -> None:
    (root / ".ddflow").mkdir()
    (root / ".ddflow" / "config.toml").write_text(f"[approvals]\npolicy = {json.dumps(FILE_POLICY)}\n")
    (root / ".ddflow" / "local").mkdir()
    (root / ".ddflow" / "local" / "config.toml").write_text(f"[approvals]\npolicy = {json.dumps(local)}\n")


def test_a_lower_layer_list_that_drops_higher_entries_is_recorded(tmp_path):
    _write_layers(tmp_path, ["accept_baseline_red"])
    cfg = Config.load(tmp_path)
    assert cfg.approvals.policy == ["accept_baseline_red"]
    note = " ".join(cfg.dropped_members)
    for dropped in ("skip_after_unavailable", "complete_unavailable", "claim_unavailable"):
        assert dropped in note, note
    assert "file layer" in note
    assert "local layer" in note


def test_the_dropped_entries_are_said_on_stderr(tmp_path, capsys):
    _write_layers(tmp_path, ["claim_unavailable"])
    Config.load(tmp_path)
    err = capsys.readouterr().err
    assert "approvals.policy" in err, err
    assert "skip_after_unavailable" in err, err


def test_a_lower_layer_that_keeps_every_higher_entry_drops_nothing(tmp_path):
    _write_layers(tmp_path, list(FILE_POLICY))
    assert Config.load(tmp_path).dropped_members == []


def test_a_lower_layer_that_empties_the_list_is_recorded(tmp_path):
    _write_layers(tmp_path, [])
    note = " ".join(Config.load(tmp_path).dropped_members)
    assert "accept_baseline_red" in note, note
