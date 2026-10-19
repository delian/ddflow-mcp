"""B1bfcaf6ebb: a fix task filed by `bug found --item X` must not hold X's phase open."""

from __future__ import annotations

from tests.test_bugs_as_items import K, seed, state


def test_fix_task_is_not_filed_under_the_found_items_phase(repo):
    seed(repo, feature=False)
    out = K.bug_found(repo, summary="late finding", item="T1", id="Bx", agent="a")
    assert out.exit == 0, out.reason
    st = state(repo)
    assert st.items["fix-Bx"].parent == "bugs"
    assert [t.id for t in st.tasks("P1") if not t.terminal] == []


def test_every_fix_task_lands_under_the_standing_bugs_phase(repo):
    seed(repo, feature=False)
    for n in ("B1", "B2"):
        assert K.bug_found(repo, summary="s " + n, item="T1", id=n, agent="a").exit == 0
    st = state(repo)
    assert st.items["fix-B1"].parent == "bugs" and st.items["fix-B2"].parent == "bugs"
