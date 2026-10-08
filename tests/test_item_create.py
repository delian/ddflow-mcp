"""One way to create a task (B-uni-item-create): ``services.items.add_task`` and the one
duplicate screen ``services.similar.screen``.

The old implementations of the screen are kept below verbatim as ORACLES (L-Bf72f9fb741:
pin every definition before unifying them), and the single screen is checked against what
the three duplicated sites used to decide.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import items as A
from ddflow.api import schedule as SCHED
from ddflow.api._dedupe import Checked
from ddflow.config import Config
from ddflow.core.model import Item, State, fold
from ddflow.infra.log import EventLog
from ddflow.infra.store import similar_records
from ddflow.services import importer as IM
from ddflow.services import importer_harness as H
from ddflow.services import items as IT
from ddflow.services import similar
from ddflow.services import triggers as TR

WHY = "a test route"


def _log(repo: Path) -> EventLog:
    (repo / ".ddflow").mkdir(exist_ok=True)
    return EventLog(repo, "agent-test")


def _data(log: EventLog, subject: str) -> list[dict]:
    return [e.data for e in log.read_all() if e.subject == subject and e.kind == "task.added"]


# -- add_task -------------------------------------------------------------------------------


def test_a_draft_writes_only_the_fields_it_sets():
    d = IT.TaskDraft("X", title="t", globs=["a/**"], extra={"fixes": ["B1"]})
    assert d.data() == {"title": "t", "globs": ["a/**"], "fixes": ["B1"]}
    assert IT.TaskDraft.from_data("X", {"title": "t", "source": "s.md"}) == IT.TaskDraft(
        "X", title="t", extra={"source": "s.md"}
    )


def test_add_task_appends_the_event_and_tells_the_state(repo):
    log, cfg, st = _log(repo), Config.load(repo), State()
    st.items["P"] = Item(id="P", kind="phase", title="p")
    out = IT.add_task(log, st, cfg, IT.TaskDraft("T1", parent="P", title="one"), dedupe=WHY)
    assert out.ok and st.items["T1"].parent == "P"
    assert (st.items["T1"].title, st.items["T1"].priority) == ("one", 100)
    assert _data(log, "T1") == [{"parent": "P", "title": "one"}]
    # a second add in the same batch sees the first
    again = IT.add_task(log, st, cfg, IT.TaskDraft("T1", title="two"), dedupe=WHY)
    assert again.refused and "already exists" in again.problem


@pytest.mark.parametrize(
    ("draft", "says"),
    [
        (IT.TaskDraft("a:b"), "colon"),
        (IT.TaskDraft("X", globs=['"src/**"']), "cannot be paths"),
        (IT.TaskDraft("X", parent="NOPE"), "no such parent"),
    ],
)
def test_add_task_refuses_what_the_adders_refuse(repo, draft, says):
    log = _log(repo)
    out = IT.add_task(log, State(), Config.load(repo), draft, dedupe=WHY)
    assert not out.ok and not out.refused and says in out.problem
    assert not _data(log, draft.id)


@pytest.mark.parametrize("dedupe", [" ", "", None])
def test_add_task_needs_the_check_or_the_reason_there_is_none(repo, dedupe):
    with pytest.raises(ValueError, match="reason"):
        IT.add_task(_log(repo), State(), Config.load(repo), IT.TaskDraft("X"), dedupe=dedupe)


def test_a_removed_id_comes_back_only_when_asked(repo):
    log, cfg, st = _log(repo), Config.load(repo), State()
    st.items["X"] = Item(id="X", kind="task", title="old")
    st.items["X"].removed = True
    refused = IT.add_task(log, st, cfg, IT.TaskDraft("X", title="new"), dedupe=WHY)
    assert refused.refused and "--readd" in refused.problem
    assert IT.add_task(log, st, cfg, IT.TaskDraft("X", title="new"), dedupe=WHY, readd=True).ok


def test_the_checks_fields_join_the_event(repo):
    log = _log(repo)
    chk = Checked(fields={"dedupe": {"answer": "new"}})
    IT.add_task(log, State(), Config.load(repo), IT.TaskDraft("X", title="t"), dedupe=chk)
    assert _data(log, "X") == [{"title": "t", "dedupe": {"answer": "new"}}]


# -- every route writes the event it always wrote ---------------------------------------------


def test_task_add_writes_its_shape_and_a_sub_task_releases_the_parents_lease(repo):
    A.phase_add(repo, "P", title="phase")
    A.task_add(repo, "T", title="parent", parent="P", globs="a/**", needs="N", priority=7)
    [ev] = _data(EventLog(repo), "T")
    assert ev == {
        "parent": "P",
        "globs": ["a/**"],
        "body": "",
        "tags": [],
        "priority": 7,
        "title": "parent",
        "needs": ["N"],
        "line": "",
    }
    from ddflow.services import leases as L

    log = EventLog(repo)
    L.acquire(log, Config.load(repo), "T", worktree=str(repo), branch="b", force=True)
    out = A.task_add(repo, "T.1", title="child", parent="T")
    assert out.data["released_parent_lease"] is True
    assert fold(EventLog(repo).read_all()).items["T"].lease is None


def test_split_children_inherit_and_the_umbrella_lets_go(repo):
    from ddflow.services import leases as L

    A.task_add(repo, "T", title="big", globs="a/**", priority=5)
    L.acquire(_log(repo), Config.load(repo), "T", worktree=str(repo), branch="b")
    out = A.split(repo, "T", into=["T.a=first", "T.b=second"], needs="Z")
    assert out.data["created"] == ["T.a", "T.b"]
    log = EventLog(repo)
    released = [e for e in log.read_all() if e.kind == "lease.released" and e.subject == "T"]
    assert len(released) == 1 and "split into T.a, T.b" in released[0].data["note"]
    assert fold(log.read_all()).items["T"].lease is None
    assert _data(log, "T.a") == [
        {"parent": "T", "title": "first", "globs": ["a/**"], "needs": ["Z"], "priority": 5}
    ]
    assert _data(log, "T.b")[0]["needs"] == []
    assert A.split(repo, "T", into=["bad:id", "ok"]).exit == 1


def test_a_split_that_cannot_finish_writes_nothing(repo):
    """Every child is checked before the first is written: a later bad or taken id leaves
    no half-split umbrella behind."""
    A.task_add(repo, "T", title="big", globs="a/**")
    A.task_add(repo, "T.a", title="taken")
    for into in (["T.c=third", "T.a=first"], ["ok", "bad:id"], ["ok", "ok"]):
        out = A.split(repo, "T", into=into)
        assert out.exit == 1 and out.data["created"] == [], into
    log = EventLog(repo)
    assert not {"T.c", "ok"} & set(fold(log.read_all()).items)


def test_a_trigger_fire_files_through_add_task_and_a_missing_phase_suppresses(repo):
    log, cfg = _log(repo), Config.load(repo)
    A.phase_add(repo, "P1", title="phase")
    job = SimpleNamespace(
        job=SimpleNamespace(id="fix", title="Fix", scope_globs=["s/**"], mode="fix", prompt="p")
    )
    defs = SimpleNamespace(jobs={"fix": job})
    good = TR.Trigger(id="t", event="gate.failed", action={"job": "fix", "phase": "P1"})
    bad = TR.Trigger(id="u", event="gate.failed", action={"job": "fix", "phase": "NOPE"})
    ds = [
        TR.Decision("t", "k", True, events=["e1"], hop=1),
        TR.Decision("u", "k", True, events=["e2"], hop=1),
        TR.Decision("ghost", "k", False, reason="debounce", events=["e3"]),
    ]

    st = fold(log.read_all())
    from datetime import UTC, datetime

    SCHED._apply(log, cfg, st, defs, {"t": good, "u": bad}, ds, datetime.now(UTC), [])
    assert [d.fire for d in ds] == [True, False, False]  # a suppression needs no trigger
    assert ds[1].reason == "item_refused" and "no such parent" in ds[1].detail
    st = fold(log.read_all())
    assert ds[0].items == ["T-t-1"] and st.items["T-t-1"].parent == "P1"
    assert "T-u-1" not in st.items
    assert st.trigger_suppressed["u"][0]["reason"] == "item_refused"


# -- the importer's tasks pass the same checks ---------------------------------------------------


def _task(ident: str, **kw) -> IM.Found:
    extra = {"phase": kw.pop("phase", "")}
    return IM.Found(
        kind="task", ident=ident, title=f"do {ident}", source="docs/todo.md:1", extra=extra, **kw
    )


def test_an_imported_task_with_a_bad_glob_parent_or_id_is_refused_not_written(repo):
    log = _log(repo)
    phase = IM.Found(kind="phase", ident="P9", title="phase", source="docs/todo.md:1")
    plan = IM.ImportPlan(
        found=[
            phase,
            _task("ok", globs=["src/**"], phase="P9"),
            _task("badglob", globs=['"src/**"']),
            _task("orphan", phase="NO-SUCH-PHASE"),
            _task("a:b"),
        ]
    )
    counts = IM.apply_import(repo, log, plan)
    items = fold(log.read_all()).items
    assert set(items) == {"P9", "ok"}
    assert counts["task"] == 1 and counts[IM._REFUSED_KEY] == 3
    [ev] = _data(log, "ok")
    assert ev["parent"] == "P9" and ev["source"] == "docs/todo.md:1" and ev["globs"] == ["src/**"]


def test_an_imported_task_does_not_overwrite_one_already_in_the_queue(repo):
    log = _log(repo)
    A.task_add(repo, "T1", title="mine")
    counts = IM.apply_import(repo, log, IM.ImportPlan(found=[_task("T1")]))
    assert counts.get("task", 0) == 0 and counts[IM._REFUSED_KEY] == 1
    assert fold(log.read_all()).items["T1"].title == "mine"


# -- the one duplicate screen against the three it replaces ---------------------------------------

_TEXTS = [
    ("Use the shared cache for all gpu worker nodes in the cluster", "cache"),
    ("Use the shared cache for all gpu worker nodes in the cluster", "cache"),
    ("use the SHARED cache for all gpu worker  nodes in the cluster", "cache"),
    ("Use the shared cache for every gpu worker node in the cluster today", "cache"),
    ("Never restart the scheduler while a merge is running on the base branch", "merge"),
    ("Compile the release notes from the changelog before cutting a version", "notes"),
]


def _queue():
    st = State()
    st.lessons["L1"] = SimpleNamespace(
        id="L1",
        title="Shared cache",
        rule="Use the shared cache for all gpu worker nodes in the cluster",
        why="",
        how="",
    )
    return st


def _first_duplicate(assessment, cfg):
    return next(
        (c for c in assessment.candidates if similar.is_duplicate(c, assessment, cfg)), None
    )


def _oracle_harness(found, state, cfg):
    """The harness `dedupe` before the unification, verbatim."""
    base = similar_records(state) if state is not None else []
    index = similar.build(base)
    kept, duplicates, seen = [], [], {}
    for f in found:
        hit = _first_duplicate(similar.assess(index, H._as_record(f), cfg), cfg)
        if hit is not None:
            duplicates.append((f.ident, hit.id, hit.score, "identical" in hit.flags, "queue"))
            continue
        key = " ".join(f.body.casefold().split())
        if key in seen:
            duplicates.append((f.ident, seen[key].ident, 1.0, True, "import"))
            continue
        seen[key] = f
        kept.append(f.ident)
    return kept, duplicates


def _found(i: int, text: str) -> IM.Found:
    return IM.Found(kind="memory", ident=f"M-{i}", title="", source="m.md", body=text)


@pytest.mark.parametrize("on_match", ["ask", "warn"])
def test_the_harness_dedupe_is_the_old_one(monkeypatch, repo, on_match):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", on_match)
    cfg = Config.load(repo)
    found = [_found(i, t) for i, (t, _) in enumerate(_TEXTS)]
    for state in (None, _queue()):
        kept, dups = H.dedupe(found, state, cfg)
        want_kept, want_dups = _oracle_harness(found, state, cfg)
        assert [f.ident for f in kept] == want_kept
        assert [(d.found.ident, d.of, d.score, d.identical, d.where) for d in dups] == want_dups


def test_with_the_check_off_the_harness_dedupe_drops_nothing(monkeypatch, repo):
    """Bug Be31dbdb811: the harness folded word-for-word copies of one batch even with
    `[dedupe] on_match = "off"`, which the knob and the docstring say drops nothing."""
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
    found = [_found(i, t) for i, (t, _) in enumerate(_TEXTS)]
    kept, dups = H.dedupe(found, _queue(), Config.load(repo))
    assert [f.ident for f in kept] == [f.ident for f in found] and dups == []


def test_records_with_no_text_are_not_copies_of_each_other(monkeypatch, repo):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    blank = [{"id": "m1", "kind": "memory"}, {"id": "m2", "kind": "memory", "body": "  "}]
    kept, reps = similar.screen(blank, None, Config.load(repo), fold_copies=True)
    assert len(kept) == 2 and reps == []


def test_screen_compares_batch_members_only_where_asked(monkeypatch, repo):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    cfg = Config.load(repo)
    a = {"id": "a", "kind": "lesson", "title": "", "body": _TEXTS[0][0], "item": ""}
    b = {**a, "id": "b"}
    assert similar.screen([a, b], None, cfg)[1] == []
    kept, reps = similar.screen([a, b], None, cfg, compare=lambda r, t: r["id"] == "b")
    assert [r["id"] for r in kept] == ["a"]
    assert [(r.of, r.where, r.identical) for r in reps] == [("a", "import", True)]
    kept, reps = similar.screen([a, b], None, cfg, compare=lambda r, t: False)
    assert len(kept) == 2 and reps == []
    kept, reps = similar.screen([a, b], None, cfg, fold_copies=True)
    assert [r.of for r in reps] == ["a"]
    # an id that collides with a stored record's is not that record
    st = _queue()
    clash = {**a, "id": "L1", "title": "Shared cache"}  # L1 under its own id
    [rep] = similar.screen([clash], st, cfg, compare=lambda r, t: True)[1]
    assert (rep.of, rep.where) == ("L1", "queue")


def test_a_done_phase_stays_open_when_the_checks_refuse_one_of_its_tasks(repo):
    log = _log(repo)
    done = IM.Found(kind="phase", ident="P8", title="phase", source="docs/todo.md:1", done=True)
    plan = IM.ImportPlan(found=[done, _task("fine", phase="P8"), _task("bad:id", phase="P8")])
    IM.apply_import(repo, log, plan)
    st = fold(log.read_all())
    assert "fine" in st.items and "bad:id" not in st.items
    assert st.items["P8"].state == "open"


def test_adding_a_second_child_does_not_release_a_lease_twice(repo):
    from ddflow.services import leases as L

    log, cfg = _log(repo), Config.load(repo)
    A.task_add(repo, "T", title="parent")
    L.acquire(log, cfg, "T", worktree=str(repo), branch="b")
    st = fold(log.read_all())
    assert IT.release_umbrella(log, st, "T", note="n") is True
    assert IT.release_umbrella(log, st, "T", note="n") is False
    assert len([e for e in log.read_all() if e.kind == "lease.released"]) == 1


def test_an_imported_branch_is_a_task_with_its_source(repo):
    log = _log(repo)
    branch = IM.Found(
        kind="branch", ident="feat-x", title="feat-x", source="git:feat-x", extra={"ahead": 3}
    )
    counts = IM.apply_import(repo, log, IM.ImportPlan(found=[branch]))
    assert counts == {"branch": 1}
    [ev] = _data(log, "feat-x")
    assert ev["source"] == "git:feat-x" and "carries 3 commit(s)" in ev["body"]
    assert set(ev) == {"title", "body", "source"}
