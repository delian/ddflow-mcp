"""[locks].review_reuse (LK-review-reuse): a review is reused after main moved when the
item's own change is unchanged (git patch-id --stable) and main's changes are disjoint from
the files the reviewer saw; overlap re-reviews only the overlapping files; any doubt falls
back to a full re-review."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from ddflow.config_sections.locks import LocksConfig
from ddflow.services import completion as C
from ddflow.services import review_reuse as RR
from ddflow.services.gates.outcomes import StaleNote
from ddflow.services.gates.reviewed_paths import path_snapshot


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
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


def _item(snapshot: dict[str, str], *, base: str = "main", branch: str = "") -> SimpleNamespace:
    rec = SimpleNamespace(outcome="passed", evidence={"reviewed_paths": snapshot})
    return SimpleNamespace(
        gates={"critic": rec, "ci": SimpleNamespace(outcome="passed", evidence={})},
        base=base,
        branch=branch,
    )


def _notes(gate: str = "critic") -> list[StaleNote]:
    return [StaleNote(gate, "it ran on abc; the working tree differs")]


# -------------------------------------------------------------------------------------- knob


def test_review_reuse_is_off_by_default_and_only_off_or_disjoint() -> None:
    from ddflow.config_sections._docs import DECLARED

    assert LocksConfig().review_reuse == "off"
    assert DECLARED["locks.review_reuse"].choices == ("off", "disjoint")
    assert DECLARED["locks.review_reuse"].strictest[0] == "off"


# --------------------------------------------------------------------------------- classify


def _entries(mine: str, other: str) -> dict[str, tuple[str, str]]:
    return {"mine.py": ("100644", mine), "other.py": ("100644", other)}


def test_classify_keeps_a_review_when_changes_are_disjoint() -> None:
    recorded = {"mine.py": "100644 aaaa"}
    v = RR.classify(recorded, ["mine.py"], _entries("aaaa", "bbbb"), "deadbeef")
    assert v.verdict == RR.KEEP
    assert "disjoint" in v.why


def test_classify_deltas_to_the_overlapping_files_on_overlap() -> None:
    recorded = {"mine.py": "100644 aaaa"}
    v = RR.classify(recorded, ["mine.py"], _entries("cccc", "bbbb"), "deadbeef")
    assert v.verdict == RR.DELTA
    assert v.overlapping == ("mine.py",)
    assert "mine.py" in v.why


def test_classify_fails_closed_when_the_patch_id_is_unknown() -> None:
    v = RR.classify({"mine.py": "100644 aaaa"}, ["mine.py"], _entries("aaaa", "bbbb"), "")
    assert v.verdict == RR.STALE


def test_classify_fails_closed_when_the_tree_is_unreadable() -> None:
    v = RR.classify({"mine.py": "100644 aaaa"}, ["mine.py"], None, "deadbeef")
    assert v.verdict == RR.STALE


def test_classify_fails_closed_on_a_new_file_the_round_never_saw() -> None:
    recorded = {"mine.py": "100644 aaaa"}
    entries = {"mine.py": ("100644", "aaaa"), "other.py": ("100644", "bbbb")}
    v = RR.classify(recorded, ["mine.py", "other.py"], entries, "deadbeef")
    assert v.verdict == RR.STALE
    assert "other.py" in v.why


# --------------------------------------------------------------------------------- own patch-id


def test_own_patch_id_is_stable_for_an_unchanged_item_change(tmp_path: Path) -> None:
    d = _repo(tmp_path)
    _git(d, "checkout", "-qb", "work")
    (d / "mine.py").write_text("2\n")
    _git(d, "commit", "-qam", "mine edit")
    first = RR.own_patch_id(d, "main", "work", ["mine.py"])
    assert first
    assert RR.own_patch_id(d, "main", "work", ["mine.py"]) == first


# --------------------------------------------------------------------------------- refine_stale_notes


def test_disjoint_reuse_keeps_a_review_after_a_disjoint_main_move(tmp_path: Path) -> None:
    d = _repo(tmp_path)
    _git(d, "checkout", "-qb", "work")
    (d / "mine.py").write_text("2\n")
    _git(d, "commit", "-qam", "item changes mine")
    it = _item(path_snapshot(d, ["mine.py"]), branch="work")
    _git(d, "checkout", "-q", "main")
    (d / "other.py").write_text("2\n")  # main moves a disjoint file
    _git(d, "commit", "-qam", "main moves other")
    _git(d, "checkout", "-q", "work")
    _git(d, "merge", "-q", "main", "-m", "merge main")
    (out,) = C.refine_stale_notes(_notes(), it, d, "", ["mine.py"], reuse="disjoint")
    assert out.reused
    assert "disjoint" in out.reused
    assert out.why == ""


def test_disjoint_overlap_keeps_the_note_and_names_only_the_overlap(tmp_path: Path) -> None:
    d = _repo(tmp_path)
    _git(d, "checkout", "-qb", "work")
    (d / "mine.py").write_text("2\n")
    _git(d, "commit", "-qam", "item changes mine")
    it = _item(path_snapshot(d, ["mine.py"]), branch="work")
    (d / "mine.py").write_text("3\n")  # the item edits the reviewed file again
    (out,) = C.refine_stale_notes(_notes(), it, d, "", ["mine.py"], reuse="disjoint")
    assert not out.reused
    assert "mine.py" in out.why


def test_off_drops_the_note_and_records_no_reuse(tmp_path: Path) -> None:
    d = _repo(tmp_path)
    _git(d, "checkout", "-qb", "work")
    (d / "mine.py").write_text("2\n")
    _git(d, "commit", "-qam", "item changes mine")
    it = _item(path_snapshot(d, ["mine.py"]), branch="work")
    _git(d, "checkout", "-q", "main")
    (d / "other.py").write_text("2\n")
    _git(d, "commit", "-qam", "main moves other")
    _git(d, "checkout", "-q", "work")
    _git(d, "merge", "-q", "main", "-m", "merge main")
    assert C.refine_stale_notes(_notes(), it, d, "", ["mine.py"]) == []
