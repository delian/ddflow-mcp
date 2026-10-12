"""Import mints readable ids and keeps the whole item text (B5d57136281, B97ee75909a,
imp-parent-id-from-heading, imp-titles-truncated-no-text)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.slug import slug_words
from ddflow.services import importer as IM

LONG = (
    "- [ ] **36.4z** Every family ships its **paired hard negative**. C2/C3 negatives\n"
    "  are wrong for a family whose positive set is defined by the absence of a marker.\n"
    "  Second line of detail.\n"
)


def _scan(tmp_path, text):
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "todo.md").write_text(text)
    found, _ = IM.scan_todos(tmp_path)
    return found


def test_slug_words_never_cuts_mid_word():
    s = slug_words("Give base room blueprints a cloud gate", 25)
    assert s == "give-base-room-blueprints"
    assert slug_words("supercalifragilistic word", 5) == "supercalifragilistic"
    assert slug_words("short", 24) == "short"


def test_derived_ids_are_whole_words(tmp_path):
    found = _scan(
        tmp_path,
        "## Phase 39 follow-ups (not started)\n\n- [ ] Give base room blueprints a cloud gate\n",
    )
    for f in found:
        for part in f.ident.replace(".", "-").split("-"):
            assert part in {"PHASE", "39", "FOLLOW", "UPS", "NOT", "STARTED"} | set(
                "give base room blueprints a cloud gate".split()
            ), f.ident
    phase = next(f for f in found if f.kind == "phase")
    assert phase.ident.endswith("STARTED"), phase.ident
    task = next(f for f in found if f.kind == "task")
    assert task.ident.endswith(".give-base-room-blueprints-a-cloud-gate"), task.ident


def test_legacy_truncated_ids_are_recorded_so_a_reimport_stays_stable(tmp_path):
    found = _scan(
        tmp_path,
        "## Phase 39 follow-ups (not started)\n\n- [ ] Give base room blueprints a cloud gate\n",
    )
    phase = next(f for f in found if f.kind == "phase")
    task = next(f for f in found if f.kind == "task")
    assert phase.extra["legacy_ident"] == "PHASE-39-FOLLOW-UPS-NOT"
    assert task.extra["legacy_ident"] == "PHASE-39-FOLLOW-UPS-NOT.give-base-room-bluep"


def test_title_is_a_whole_sentence_and_body_carries_the_text(tmp_path):
    (task,) = [f for f in _scan(tmp_path, "## P\n\n" + LONG) if f.kind == "task"]
    assert task.title == "Every family ships its paired hard negative."
    assert "absence of a marker." in task.body
    assert "Second line of detail." in task.body
    assert task.ident == "36.4z"


def test_a_long_sentence_is_cut_at_a_word_with_no_open_backtick(tmp_path):
    text = "## P\n\n- [ ] " + "word " * 18 + "`some/path.py` " + "tail " * 20 + "\n"
    (task,) = [f for f in _scan(tmp_path, text) if f.kind == "task"]
    assert len(task.title) <= 121
    assert task.title.count("`") % 2 == 0
    assert task.title.endswith("…")
    assert not task.title[:-1].endswith("wor")
    assert "tail tail" in task.body


def test_apply_writes_the_body(tmp_path):
    (found,) = [f for f in _scan(tmp_path, "## P\n\n" + LONG) if f.kind == "task"]
    assert IM.task_body(found).endswith(f"Imported from {found.source}.")
    assert "Second line" in IM.task_body(found)


def test_reimport_keeps_the_id_an_older_import_minted(tmp_path):
    found = _scan(
        tmp_path,
        "## Phase 39 follow-ups (not started)\n\n- [ ] Give base room blueprints a cloud gate\n",
    )
    old_phase, old_task = "PHASE-39-FOLLOW-UPS-NOT", "PHASE-39-FOLLOW-UPS-NOT.give-base-room-bluep"
    IM._keep_imported_ids(found, {old_phase, old_task})
    assert [f.ident for f in found] == [old_phase, old_task]
    assert found[1].extra["phase"] == old_phase


def test_a_numbered_heading_is_not_cut_at_its_number():
    assert IM._title_of("39. Follow-ups not followed up") == "39. Follow-ups not followed up"
    assert IM._title_of("Fix it, e.g. Foo bar. Then more") == "Fix it, e.g. Foo bar."


def test_repeats_in_an_old_queue_keep_distinct_ids(tmp_path):
    text = "## One\n\n- [ ] Give base room blue paint\n- [ ] Give base room blue primer\n"
    found = _scan(tmp_path, text)
    old = {"ONE", "ONE.give-base-room-blue", "ONE.give-base-room-blue-2"}
    IM._keep_imported_ids(found, old)
    ids = [f.ident for f in found if f.kind == "task"]
    assert ids == ["ONE.give-base-room-blue", "ONE.give-base-room-blue-2"]


def test_a_lettered_heading_is_not_cut_at_its_letter():
    assert IM._title_of("A. Appendix tuning") == "A. Appendix tuning"
    assert IM._title_of("Written by A. Smith") == "Written by A. Smith"


def test_common_abbreviations_do_not_end_a_title():
    assert IM._title_of("No. 5 do the thing. Then more") == "No. 5 do the thing."
    assert IM._title_of("Dr. Smith wrote it. Then left") == "Dr. Smith wrote it."
