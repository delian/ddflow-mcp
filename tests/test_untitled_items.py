"""doctor notes an item whose title says nothing but its id.

Bug B9e8ca361ea: the backlog migration (6be308a) wrote 24 items titled with nothing but
their own id (`B30`, `B157`), and several more whose title was a fragment (`+ B159`).
They showed that way in `status`, `board`, `brief` and every gate prompt, and nothing
said so: every check measured the items that were present, none asked whether an item
could be told apart from its number. The titles are corrected as `task.updated` events;
this note is what makes the next migration's slip visible the day it lands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _notes(repo) -> list[str]:
    _code, out, err = run_cli(repo, "--json", "doctor")
    return json.loads(out or err)["notes"]


def _untitled(repo) -> str:
    return "\n".join(n for n in _notes(repo) if "no title of their own" in n)


def test_an_item_titled_with_its_own_id_is_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "B30", "--title", "B30")[0] == 0
    note = _untitled(repo)
    assert "B30" in note, _notes(repo)
    assert "ddflow update" in note and "--title" in note, note


def test_an_empty_title_is_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1")[0] == 0
    assert "T1" in _untitled(repo), _notes(repo)


def test_a_real_title_is_not_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "fix the parser")[0] == 0
    assert _untitled(repo) == ""


def test_a_removed_item_is_not_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "B30", "--title", "B30")
    assert run_cli(repo, "remove", "B30", "--reason", "gone")[0] == 0
    assert _untitled(repo) == ""


def test_many_are_one_note_not_a_wall(repo):
    """The migration left 24 at once; one line per item would bury every other note."""
    assert run_cli(repo, "init")[0] == 0
    for n in range(30, 42):
        run_cli(repo, "task", "add", f"B{n}", "--title", f"B{n}")
    lines = [n for n in _notes(repo) if "no title of their own" in n]
    assert len(lines) == 1, lines
    assert "\n" not in lines[0], lines[0]
    assert lines[0].startswith("12 item(s)"), lines[0]


def test_a_title_of_only_id_tokens_is_noted(repo):
    """Critic on B212: the migration also wrote `+ B159` (for B158) and `B62-B64`-shaped
    leftovers -- ids and punctuation, not a title -- and an exact-id test missed them."""
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "B158", "--title", "+ B159")
    run_cli(repo, "task", "add", "B62", "--title", "B62–B64")  # noqa: RUF001 -- the en dash the migration wrote
    run_cli(repo, "task", "add", "B30", "--title", "B30.")
    note = _untitled(repo)
    for iid in ("B158", "B62", "B30"):
        assert iid in note, note


def test_a_short_real_title_with_a_number_is_not_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "Python 3.13 support")
    run_cli(repo, "task", "add", "T2", "--title", "fix B159")
    assert _untitled(repo) == ""
