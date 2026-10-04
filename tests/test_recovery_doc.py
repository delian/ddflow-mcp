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
    """One phrase per band `recover` flags (`views.markdown.recovery_band`); a band added
    there without a phrase here fails, so the runbook cannot silently fall behind
    (roborev 1507)."""
    from ddflow.services.leases import Recovery
    from ddflow.views import markdown as M

    phrase = {
        M._HOLDING: "**measured** to hold it",
        M._UNMEASURED: "**could\nnot measure**",
        M._UNHELD: "RUNNING with nobody on it",
    }
    # Every band an entry can land in, from every kind x salvageable value.
    bands = {
        M.recovery_band(Recovery(item="x", holder="h", kind=k, salvageable=s))
        for k in ("expired_lease", "orphan_worktree", "stale_running")
        for s in (True, False, None)
    } - {None}
    assert bands == set(phrase), f"a band without a runbook phrase: {bands - set(phrase)}"
    para = _flag_paragraph()
    for band in bands:
        assert " ".join(phrase[band].split()) in para, (band, para)
    assert "were **measured** to contain work" not in para, para
