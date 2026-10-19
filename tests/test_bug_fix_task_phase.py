"""B1bfcaf6ebb: a fix task filed by `bug found --item X` must not hold X's phase open."""

from __future__ import annotations

from tests.test_bugs_as_items import K, seed, state


def test_fix_task_is_not_filed_under_the_found_items_phase(repo):
    seed(repo, feature=False)
    out = K.bug_found(repo, summary="late finding", item="T1", id="Bx", agent="a")
    assert out.exit == 0, out.reason
    st = state(repo)
    assert st.items["fix-Bx"].parent != "P1"
    assert [t.id for t in st.tasks("P1") if not t.terminal] == []
