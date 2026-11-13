"""Bde9090f916: `ddflow session note --item` reports the item, not only the session.

A note recorded with ``--item`` was attached to the item but the answer named only the
session, so the caller could not confirm the note reached the item it named. The outcome
and the command's text must both carry the item.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_session_note_reports_the_item_it_was_attached_to(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "--json", "session", "note", "--text", "changed approach", "--item", "P1.T1"
    )
    assert code == 0, err
    data = json.loads(out)
    assert data["item"] == "P1.T1"
    assert data["session"]


def test_session_note_text_names_the_item(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "session", "note", "--text", "changed approach", "--item", "P1.T1"
    )
    assert code == 0, err
    assert "P1.T1" in out
