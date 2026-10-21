"""B-complete-different-tree-note-noisy: complete's "passed on a different tree" note for a
review gate compares only the paths the item changed (the round's `reviewed_paths`), and
names the ones that moved; a merge of main or an unrelated edit raises no note."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from ddflow.services import completion as C
from ddflow.services.gates.outcomes import StaleNote
from ddflow.services.gates.reviewed_paths import path_snapshot


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _tree(tmp_path: Path) -> Path:
    d = tmp_path / "t"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "a@b")
    _git(d, "config", "user.name", "a")
    (d / "mine.py").write_text("1\n")
    (d / "other.py").write_text("1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    return d


def _item(snapshot: dict[str, str]):
    rec = SimpleNamespace(outcome="passed", evidence={"reviewed_paths": snapshot})
    return SimpleNamespace(
        gates={"critic": rec, "ci": SimpleNamespace(outcome="passed", evidence={})}
    )


def _notes(gate: str = "critic") -> list[StaleNote]:
    return [StaleNote(gate, "it ran on abc; the working tree differs in other.py")]


def test_an_unrelated_change_raises_no_note(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    (d / "other.py").write_text("2\n")  # main moved a file the item never touched
    assert C.refine_stale_notes(_notes(), it, d, "", ["mine.py"]) == []


def test_a_changed_reviewed_path_is_named(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    (d / "mine.py").write_text("2\n")
    (d / "other.py").write_text("2\n")
    (out,) = C.refine_stale_notes(_notes(), it, d, "", ["mine.py"])
    assert "mine.py" in out.why and "other.py" not in out.why


def test_a_path_the_item_changes_now_and_the_round_never_saw_is_named(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    (d / "other.py").write_text("2\n")
    (out,) = C.refine_stale_notes(_notes(), it, d, "", ["mine.py", "other.py"])
    assert "other.py" in out.why


def test_a_landed_commit_is_compared_by_its_own_tree(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    (d / "other.py").write_text("2\n")
    _git(d, "commit", "-qam", "unrelated")
    head = subprocess.run(
        ["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert C.refine_stale_notes(_notes(), it, d, head, ["mine.py"]) == []


def test_a_gate_without_reviewed_paths_or_unknown_changes_keeps_its_note(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    assert C.refine_stale_notes(_notes("ci"), it, d, "", ["mine.py"]) == _notes("ci")
    assert C.refine_stale_notes(_notes(), it, d, "", None) == _notes()


def test_a_new_empty_file_is_not_mistaken_for_an_absent_path(tmp_path):
    d = _tree(tmp_path)
    it = _item(path_snapshot(d, ["mine.py"]))
    (d / "new.txt").write_text("")  # empty: its entry is still `mode oid`, never ""
    (out,) = C.refine_stale_notes(_notes(), it, d, "", ["mine.py", "new.txt"])
    assert "new.txt" in out.why
