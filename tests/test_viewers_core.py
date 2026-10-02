"""The shared list engine for the task/phase/bug/research/session viewers.

Pure over a folded State, so the filters are tested on hand-built records; one test runs
the api wrapper over a real log to prove it reads what the writers wrote.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.viewers import view_list
from ddflow.config import Config
from ddflow.core.model import Bug, Item, Lease, ResearchNote, Session, State
from ddflow.services import viewers as V


def _state() -> State:
    st = State()
    st.items["P1"] = Item(
        id="P1", kind="phase", title="Phase one", created_at="2026-01-01T00:00:00"
    )
    st.items["P2"] = Item(
        id="P2", kind="phase", title="Phase two", created_at="2026-01-02T00:00:00"
    )
    st.items["T1"] = Item(
        id="T1", kind="task", title="First", parent="P1", tags=["a"], state="done",
        created_at="2026-02-01T00:00:00", completed_at="2026-03-01T00:00:00",
    )  # fmt: skip
    st.items["T2"] = Item(
        id="T2", kind="task", title="Second", parent="P1", tags=["a", "b"],
        created_at="2026-02-02T00:00:00",
        lease=Lease(holder="alice", acquired_at=0.0, renewed_at=0.0, ttl_s=60),
    )  # fmt: skip
    st.items["T3"] = Item(
        id="T3", kind="task", title="Third", parent="P2", created_at="2026-02-03T00:00:00"
    )
    st.items["T4"] = Item(
        id="T4",
        kind="task",
        title="Gone",
        parent="P2",
        removed=True,
        created_at="2026-02-04T00:00:00",
    )
    st.bugs["B1"] = Bug(
        id="B1", item="T1", summary="broken\nsecond line", found_at="2026-04-01T00:00:00"
    )
    st.bugs["B2"] = Bug(
        id="B2",
        item="T3",
        summary="old",
        found_at="2026-04-02T00:00:00",
        fixed_at="2026-04-03T00:00:00",
    )
    st.research["R1"] = ResearchNote(
        id="R1",
        question="why?",
        verdict="CONFIRMED",
        tags=["x"],
        at="2026-05-01T00:00:00",
        item="T2",
    )
    st.sessions["S1"] = Session(
        id="S1", agent="alice", model="m", started_at="2026-06-01T00:00:00",
        prompts=[{"text": "do the thing"}],
    )  # fmt: skip
    st.sessions["S2"] = Session(
        id="S2", agent="bob", started_at="2026-06-02T00:00:00", ended_at="2026-06-03T00:00:00"
    )
    return st


CFG = Config.load()


def ids(kind, **kw):
    return [r["id"] for r in V.list_view(_state(), CFG, kind, **kw).rows]


def test_kinds_list_and_removed_items_are_hidden():
    assert sorted(ids("task")) == ["T1", "T2", "T3"]
    assert sorted(ids("phase")) == ["P1", "P2"]
    assert sorted(ids("bug")) == ["B1", "B2"]
    assert ids("research") == ["R1"]
    assert sorted(ids("session")) == ["S1", "S2"]


def test_unknown_kind_is_refused():
    with pytest.raises(V.ViewError, match="unknown kind"):
        V.list_view(_state(), CFG, "decision")


def test_filters_narrow():
    assert ids("task", state="done") == ["T1"]
    assert sorted(ids("task", phase="P1")) == ["T1", "T2"]
    assert ids("task", tag="b") == ["T2"]
    assert ids("task", agent="alice") == ["T2"]
    assert ids("bug", state="fixed") == ["B2"]
    assert ids("bug", state="open") == ["B1"]
    assert ids("bug", phase="P1") == ["B1"]
    assert ids("research", tag="x") == ["R1"]
    assert ids("research", state="confirmed") == ["R1"]
    assert ids("session", agent="bob") == ["S2"]
    assert ids("session", state="ended") == ["S2"]
    assert ids("bug", since="2026-04-02T12:00:00") == ["B2"]


def test_filter_a_kind_cannot_honour_is_refused_not_ignored():
    with pytest.raises(V.ViewError, match="no agent"):
        V.list_view(_state(), CFG, "bug", agent="alice")
    with pytest.raises(V.ViewError, match="no tag"):
        V.list_view(_state(), CFG, "session", tag="a")


def test_unknown_phase_is_refused():
    with pytest.raises(V.ViewError, match="no phase"):
        V.list_view(_state(), CFG, "task", phase="NOPE")


def test_ordering_is_newest_first_with_id_tiebreak_and_stable():
    st = State()
    for i in ("b", "a", "c"):
        st.items[i] = Item(id=i, kind="task", created_at="2026-01-01T00:00:00")
    st.items["z"] = Item(id="z", kind="task", created_at="2026-02-01T00:00:00")
    first = [r["id"] for r in V.list_view(st, CFG, "task").rows]
    assert first == ["z", "a", "b", "c"]
    assert first == [r["id"] for r in V.list_view(st, CFG, "task").rows]


def test_limit_truncates_and_says_so():
    v = V.list_view(_state(), CFG, "task", limit=2)
    assert len(v.rows) == 2 and v.total == 3 and v.truncated
    assert not V.list_view(_state(), CFG, "task", limit=3).truncated
    with pytest.raises(V.ViewError, match="limit"):
        V.list_view(_state(), CFG, "task", limit=0)


def test_rows_are_one_line_of_facts():
    row = V.list_view(_state(), CFG, "bug", state="open").rows[0]
    assert row["title"] == "broken"
    assert set(row) >= {"id", "kind", "state", "title", "owner", "updated"}


def test_secrets_in_titles_are_redacted():
    st = State()
    st.items["T"] = Item(
        id="T", kind="task", title="use token ghp_" + "a" * 36 + " here", created_at="2026-01-01"
    )
    title = V.list_view(st, CFG, "task").rows[0]["title"]
    assert "ghp_" + "a" * 10 not in title


def test_api_reads_a_real_log_and_reports_empty_and_refusal(repo):
    run_cli(repo, "init")
    out = view_list(repo, "task")
    assert out.exit == 2 and "No task records" in out.reason and out.data["rows"] == []
    assert run_cli(repo, "phase", "add", "PH", "--title", "A phase")[0] == 0
    assert run_cli(repo, "task", "add", "TK", "--title", "A task", "--phase", "PH")[0] == 0
    out = view_list(repo, "task", phase="PH")
    assert out.exit == 0 and [r["id"] for r in out.data["rows"]] == ["TK"]
    assert out.data["truncated"] is False and out.data["total"] == 1
    assert view_list(repo, "nonsense").exit == 3
