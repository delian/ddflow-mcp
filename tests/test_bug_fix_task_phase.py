"""Be8ca1270a5: a fix task filed by `bug found --item X` inherits X's open phase.

A bug found while working an item blocks that item's phase, so its fix task belongs there
(r-task-phase-needs-wave); only a stray bug, a removed source or a finished phase falls
back to the standing `bugs` phase (B1bfcaf6ebb, which first sent every fix task there).
"""

from __future__ import annotations

from tests.test_bugs_as_items import K, seed, state


def test_fix_task_inherits_the_found_items_phase(repo):
    seed(repo, feature=False)
    out = K.bug_found(repo, summary="late finding", item="T1", id="Bx", agent="a")
    assert out.exit == 0, out.reason
    st = state(repo)
    assert st.items["fix-Bx"].parent == "P1"


def test_every_stray_fix_task_lands_under_the_standing_bugs_phase(repo):
    seed(repo, feature=False)
    for n in ("B1", "B2"):
        assert K.bug_found(repo, summary="s " + n, id=n, agent="a").exit == 0
    st = state(repo)
    assert st.items["fix-B1"].parent == "bugs" and st.items["fix-B2"].parent == "bugs"
