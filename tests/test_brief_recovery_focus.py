"""B-brief-recover-eats-budget: a focused brief recovers its own item first.

When `ddflow brief item=X` is given and X is itself among the recoverable entries, the
RESUME block used to list up to five UNRELATED crashed items (the per-band cap) and bury
X's own entry alphabetically among them, and because the slice is reserved from the
budget it crowded out the focused item's own "Current" detail -- every section behind it
became a "[N more cut]" line. With the item's own recovery entry present:
- the item's own entry renders FIRST; and
- every OTHER recovery entry collapses to one count line instead of expanding.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ddflow.services.leases import Recovery
from ddflow.views.markdown import _brief_recovery

RESUME = "## ⚠ RESUME: recoverable work found"


def _rec(item, kind, salvageable):
    return Recovery(item=item, holder="h", kind=kind, salvageable=salvageable, advice="a")


def _render(recovery, *, item=""):
    out: list[str] = []
    _brief_recovery(out, recovery, item=item)
    return "\n".join(out)


def test_focused_items_own_entry_comes_first_and_others_collapse_to_a_count():
    own = _rec("FOCUS", "expired_lease", True)
    others = [_rec(f"W{i}", "orphan_worktree", True) for i in range(5)]
    text = _render([*others, own], item="FOCUS")
    assert RESUME in text, text
    # Own entry first, BEFORE the unrelated count line, so a resume acts on it first.
    assert "**FOCUS**" in text and "**FOCUS** (expired_lease" in text, text
    assert text.index("**FOCUS**") < text.index("other recoverable"), text
    # EVERY unrelated entry collapses -- not just the first one (an implementation that
    # hides only W0 while still expanding W1..W4 would still crowd out Current).
    for i in range(5):
        assert f"**W{i}**" not in text, text
    assert "5 other recoverable entries" in text, text


def test_unrelated_still_counted_into_a_single_line_for_one_other_entry():
    own = _rec("FOCUS", "expired_lease", True)  # holding band -> the item's own entry
    unmeasured = _rec("U1", "expired_lease", None)  # unmeasured band -> unrelated
    text = _render([own, unmeasured], item="FOCUS")
    # U1 is an unrelated banded entry: counted, not expanded (the singular line).
    assert "**FOCUS**" in text, text
    assert "**U1**" not in text, text
    assert "1 other recoverable entry" in text, text


def test_a_focus_with_no_own_entry_keeps_the_per_band_list():
    """The crash leftovers are other items': warning about them is the slice's job, so a
    focus with no own entry still lists them per band (unchanged)."""
    work = [_rec(f"W{i}", "orphan_worktree", True) for i in range(7)]
    text = _render([_rec("S1", "stale_running", False), *work], item="T9")
    assert "**W0**" in text and "**W4**" in text and "**W5**" not in text, text
    assert "2 more holding work" in text, text
    assert "**S1**" in text, text


# -- the full brief path (the reported bug, end to end) -------------------------------


def test_the_full_brief_counts_unrelated_and_keeps_the_current_section(repo):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    from ddflow.api import lifecycle as LC
    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ["T9", "W0", "W1", "W2", "W3"]:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}.py"]})
    # T9 is the focused item and it crashed: RUNNING with nobody on it.
    log.append("item.started", "T9", {})
    for i in range(4):
        claim = LC.claim(repo, f"W{i}", agent="crashed")
        wt = Path(claim.data["worktree"])
        (wt / "work.py").write_text("x = 1\n")
        log.append(
            "lease.acquired",
            f"W{i}",
            {
                "holder": "crashed",
                "at": 1900.0,
                "ttl_s": 60,
                "worktree": str(wt),
                "branch": claim.data["branch"],
            },
        )
    out = LC.brief(repo, item="T9", check_recovery=True, agent="next")
    assert out.exit == 0, out.reason
    text = out.data["text"]
    end = text.index("# ddflow brief")
    region = text[text.index(RESUME) : end]
    # The focused item's own entry first, unrelated counted, none expanded.
    assert region.index("**T9**") < region.index("other recoverable"), region
    assert "4 other recoverable entries" in region, region
    for i in range(4):
        assert f"**W{i}**" not in region, region
    # The focused item's own detail survives with its body, not only a heading.
    assert "## Current: T9" in text, text
    assert "- gates remaining:" in text, text
