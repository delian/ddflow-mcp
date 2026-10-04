"""docs/RECOVERY.md says what recover's `!!` flag marks (B7819fb6017).

Since B3f8c406fea, `recover` flags a tree git could not measure and an item RUNNING with
nobody on it as well as a tree measured to hold work. The runbook still said entries
marked `!!` "were measured to contain work", so a reader took a flagged tree git could
not read for one with measured work in it.
"""

from __future__ import annotations

from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "RECOVERY.md"


def _flag_paragraph() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("Entries marked `!!`")
    return " ".join(text[start : text.index("\n\n", start)].split())


def test_the_runbook_names_every_kind_of_entry_recover_flags():
    para = _flag_paragraph()
    assert "could not measure" in para.lower(), para
    assert "RUNNING with nobody on it" in para, para
    assert "were **measured** to contain work" not in para, para
