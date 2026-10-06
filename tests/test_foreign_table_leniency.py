"""An older ddflow reading a newer checkout's [gate.*], [[macro]] or [[companion]] block
warns and skips the unknown field instead of refusing (B0016a65167).

config.toml's own sections are lenient outside the code tree (B9cb7dd1c3b) and so is
[[reviewer]] (B6f757e18cf), but gates, macros and companions still raised on any field
they did not know: a gate field added in a newer release stopped every command of an
older clone, against D-compat (2). Same rule now for all of them: strict in the tree
the code came from, where an unknown key can only be a typo; lenient elsewhere, with a
warning naming the key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow import config as C
from ddflow.config import Config
from ddflow.services import companions, macros
from ddflow.services.gates import defs as G

GATE = '[gate.unit_tests]\ncommand = "pytest -q"\nknob_from_the_future = 3\n'
MACRO = '[[macro]]\nname = "mine"\nprompt = "p"\nknob_from_the_future = 3\n'
COMPANION = '[[companion]]\nid = "mine"\ntitle = "x"\nknob_from_the_future = 3\n'


def _write(root: Path, text: str) -> None:
    (root / ".ddflow").mkdir(exist_ok=True)
    (root / ".ddflow" / "config.toml").write_text(text)


def test_a_newer_gate_field_is_skipped_with_a_warning(tmp_path, capsys):
    _write(tmp_path, GATE)  # tmp_path is not the tree this code runs from
    gates = G.load_gates(tmp_path, Config())
    assert gates["unit_tests"].command == "pytest -q"
    err = capsys.readouterr().err
    assert "knob_from_the_future" in err and "newer than this code" in err


def test_a_newer_macro_field_is_skipped_with_a_warning(tmp_path, capsys):
    _write(tmp_path, MACRO)
    got = macros.load_macros(tmp_path)
    assert got["mine"].prompt == "p"
    assert "knob_from_the_future" in capsys.readouterr().err


def test_a_newer_companion_field_is_skipped_with_a_warning(tmp_path, capsys):
    _write(tmp_path, COMPANION)
    got = {c.id: c for c in companions.load(tmp_path)}
    assert got["mine"].title == "x"
    assert "knob_from_the_future" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text, load",
    [
        (GATE, lambda r: G.load_gates(r, Config())),
        (MACRO, macros.load_macros),
        (COMPANION, companions.load),
    ],
)
def test_in_the_code_tree_an_unknown_field_is_still_an_error(tmp_path, monkeypatch, text, load):
    monkeypatch.setattr(C, "_CODE_TREE", tmp_path.resolve())
    _write(tmp_path, text)
    with pytest.raises(ValueError, match="knob_from_the_future"):
        load(tmp_path)
