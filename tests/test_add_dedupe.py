"""Every add checks for an existing record first (B-add-checks-duplicates, decision
D-no-duplicates). The engine's accuracy is tests/test_similar.py; this is what an add DOES
with the answer: refuse, extend an open record, file a linked one, auto-link exact copies,
record every answer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow import api as A
from ddflow.api import _dedupe as DD
from ddflow.api.decisions import Draft
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

FIX = {
    json.loads(line)["id"]: json.loads(line)
    for line in (Path(__file__).parent / "fixtures/dedupe/corpus.jsonl").read_text().splitlines()
}
ORIGINAL = FIX["B5d98a4da0a"]["body"]
REPORT = FIX["B3eda99e0fe"]["body"]


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


@pytest.fixture(scope="session")
def corpus_repo(tmp_path_factory):
    """A repository whose log holds the fixture's records except B3eda99e0fe (all
    open): the engine weighs words by how rare they are IN A LOG, so the acceptance case
    needs a real-sized one, not two records."""
    r = tmp_path_factory.mktemp("corpus") / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    log = EventLog(r, "seed")
    for rec in FIX.values():
        if rec["added"] >= FIX["B3eda99e0fe"]["added"]:
            continue
        if rec["kind"] == "bug":
            log.append("bug.found", rec["id"], {"item": "", "summary": rec["body"]})
        else:
            log.append(
                f"{rec['kind']}.added",
                rec["id"],
                {"title": rec["title"], "body": rec["body"], "needs": [], "globs": []},
            )
    return r


@pytest.fixture
def filed(corpus_repo, tmp_path):
    repo = tmp_path / "proj"
    shutil.copytree(corpus_repo, repo)
    return repo


def state(repo):
    return fold(EventLog(repo, "a").read_all(), strict=False)


def test_refiling_a_real_report_is_refused_with_the_original_as_candidate(filed):
    out = A.bug_found(filed, summary=REPORT, id="B3eda99e0fe", agent="a")
    assert out.exit == 3, out
    assert "possible duplicate" in out.reason
    ids = [c["id"] for c in out.data["candidates"]]
    assert ids[0] == "B5d98a4da0a"
    top = out.data["candidates"][0]
    assert set(top) >= {"id", "kind", "title", "state", "score", "shared"}
    assert {"relation": "extends", "target": "B5d98a4da0a"} in out.data["options"]
    assert "B3eda99e0fe" not in state(filed).bugs, "nothing was written"


def test_extends_an_open_bug_adds_a_record_and_keeps_the_summary(filed):
    out = A.bug_found(
        filed,
        summary=REPORT,
        id="B3eda99e0fe",
        answer=DD.Answer("extends", "B5d98a4da0a"),
        agent="a",
    )
    assert out.exit == 0 and out.data["extended"] == "B5d98a4da0a"
    st = state(filed)
    assert "B3eda99e0fe" not in st.bugs, "no new id"
    assert st.bugs["B5d98a4da0a"].summary == ORIGINAL
    (add,) = st.links["B5d98a4da0a"].extensions
    assert add["text"] == REPORT and add["who"] and add["score"] > 0.5


def test_extends_a_fixed_bug_files_a_new_linked_bug(filed):
    log = EventLog(filed, "a")
    log.append("bug.fixed", "B5d98a4da0a", {"regression_test": "t::x"})
    out = A.bug_found(
        filed,
        summary=REPORT,
        id="B3eda99e0fe",
        answer=DD.Answer("duplicate_of", "B5d98a4da0a"),
        agent="a",
    )
    assert out.exit == 0 and out.data["id"] == "B3eda99e0fe"
    assert out.data["holder"]["state"] == "fixed"
    st = state(filed)
    assert st.bugs["B3eda99e0fe"].summary == REPORT
    assert st.links["B3eda99e0fe"].linked("duplicate_of") == {"B5d98a4da0a"}
    assert not st.links.get("B5d98a4da0a") or not st.links["B5d98a4da0a"].extensions


def test_exact_refiling_is_linked_without_asking(filed):
    out = A.bug_found(filed, summary=ORIGINAL.upper(), id="Bcopy", agent="a")
    assert out.exit == 0 and out.data["extended"] == "B5d98a4da0a"
    assert out.data["auto"] is True
    assert state(filed).links["B5d98a4da0a"].extensions[0]["text"] == ORIGINAL.upper()


def test_exact_refiling_of_a_closed_record_is_a_new_linked_record(filed):
    EventLog(filed, "a").append("bug.fixed", "B5d98a4da0a", {"regression_test": "t::x"})
    out = A.bug_found(filed, summary=ORIGINAL, id="Bcopy", agent="a")
    assert out.exit == 0 and out.data["id"] == "Bcopy"
    st = state(filed)
    assert st.links["Bcopy"].linked("duplicate_of") == {"B5d98a4da0a"}
    assert st.links["Bcopy"].answer["auto"] is True


def test_new_is_answered_and_recorded(filed):
    out = A.bug_found(filed, summary=REPORT, id="B3eda99e0fe", answer=DD.Answer("new"), agent="a")
    assert out.exit == 0 and "B3eda99e0fe" in state(filed).bugs
    ans = state(filed).links["B3eda99e0fe"].answer
    assert ans["answer"] == "new" and ans["candidates"][0]["id"] == "B5d98a4da0a"
    assert ans["score"] > 0.5


def test_related_links_both_ways(filed):
    out = A.bug_found(
        filed,
        summary=REPORT,
        id="B3eda99e0fe",
        answer=DD.Answer("related", "B5d98a4da0a"),
        agent="a",
    )
    assert out.exit == 0
    st = state(filed)
    assert st.links["B3eda99e0fe"].linked("related") == {"B5d98a4da0a"}
    assert st.links["B5d98a4da0a"].linked("related") == {"B3eda99e0fe"}


def test_extending_a_claimed_task_files_a_new_linked_task(repo):
    run_cli(repo, "init")
    title = "update must widen the held lease globs as well as the item globs on a claimed item"
    assert A.task_add(repo, "T1", title=title, agent="a").exit == 0
    assert A.claim(repo, "T1", no_worktree=True, agent="a").exit == 0
    refused = A.task_add(repo, "T2", title=title + " too", agent="b")
    assert refused.exit == 3 and refused.data["candidates"][0]["id"] == "T1"
    assert refused.data["candidates"][0]["state"].startswith("claimed")
    out = A.task_add(repo, "T2", title=title + " too", answer=DD.Answer("extends", "T1"), agent="b")
    assert out.exit == 0 and out.data["id"] == "T2" and out.data["holder"]["id"] == "T1"
    assert out.data["holder"]["holder"] == "a"
    assert state(repo).links["T2"].linked("extends") == {"T1"}


def test_extending_an_open_task_appends_to_it(repo):
    run_cli(repo, "init")
    title = "update must widen the held lease globs as well as the item globs on a claimed item"
    A.task_add(repo, "T1", title=title, agent="a")
    out = A.task_add(repo, "T2", title=title + " too", answer=DD.Answer("extends", "T1"), agent="b")
    assert out.exit == 0 and out.data["extended"] == "T1"
    st = state(repo)
    assert "T2" not in st.items and st.items["T1"].title == title
    assert st.links["T1"].extensions[0]["text"].endswith("too")


def test_a_bug_filed_against_the_task_that_fixes_it_asks_nothing(repo):
    run_cli(repo, "init")
    title = "update must widen the held lease globs as well as the item globs on a claimed item"
    A.task_add(repo, "T1", title=title, agent="a")
    same = A.bug_found(repo, summary=title, item="T1", id="Bx", agent="a")
    assert same.exit == 0 and same.data["id"] == "Bx", same.reason
    assert same.data["candidates"][0]["id"] == "T1"
    assert "filed_against" in same.data["candidates"][0]["flags"]
    # a second bug of the same words is another matter: it matches the first bug
    again = A.bug_found(repo, summary=title + " today", item="T1", id="By", agent="a")
    assert again.exit == 3 and again.data["candidates"][0]["id"] == "Bx"


def test_below_the_ask_threshold_the_add_goes_through_and_lists_candidates(repo):
    run_cli(repo, "init")
    A.task_add(
        repo, "T1", title="render the status board as markdown tables with gate columns", agent="a"
    )
    out = A.task_add(repo, "T2", title="colour the board columns by gate state", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["candidates"][0]["id"] == "T1" and out.data["candidates"][0]["score"] < 0.55


@pytest.mark.parametrize("mode,refuses", [("ask", True), ("warn", False), ("off", False)])
def test_on_match(filed, monkeypatch, mode, refuses):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", mode)
    out = A.bug_found(filed, summary=REPORT, id="B3eda99e0fe", agent="a")
    assert (out.exit == 3) is refuses
    if mode == "warn":
        assert out.data["candidates"][0]["id"] == "B5d98a4da0a"
        assert "B3eda99e0fe" in state(filed).bugs
    if mode == "off":
        assert "candidates" not in out.data and "B3eda99e0fe" in state(filed).bugs


def test_warn_never_merges_an_exact_copy(filed, monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "warn")
    out = A.bug_found(filed, summary=ORIGINAL, id="Bcopy", agent="a")
    assert out.exit == 0 and out.data["id"] == "Bcopy" and "Bcopy" in state(filed).bugs


def test_repeating_an_existing_id_keeps_its_own_rule(filed):
    out = A.bug_found(filed, summary=ORIGINAL, id="B5d98a4da0a", agent="a")
    assert out.exit == 0 and out.data["id"] == "B5d98a4da0a" and "extended" not in out.data
    run_cli(filed, "task", "add", "T1", "--title", "alpha beta gamma delta")
    code, _out, err = run_cli(filed, "task", "add", "T1", "--title", "alpha beta gamma delta")
    assert code == 3 and "already" in (err + _out).lower()


def test_an_unknown_target_or_answer_is_a_failure_not_a_write(filed):
    out = A.bug_found(
        filed, summary=REPORT, id="Bz", answer=DD.Answer("extends", "Bnope"), agent="a"
    )
    assert out.exit == 1 and "Bz" not in state(filed).bugs
    out = A.bug_found(filed, summary=REPORT, id="Bz", answer=DD.Answer("extends"), agent="a")
    assert out.exit == 1
    assert DD.Answer.parse("duplicate B1") == DD.Answer("duplicate_of", "B1")


def test_every_add_kind_runs_the_check(repo):
    run_cli(repo, "init")
    texts = {
        "lesson": "always record the regression test before the fix because a fix without a "
        "failing test proves nothing about the unfixed code",
        "decision": "the event log is committed to git and merged with union semantics so "
        "every clone appends its own shard and never rewrites another",
        "research": "does stdlib cosine similarity over tokenized titles find duplicate "
        "records faster than any external embedding model on this corpus",
        "memory": "the continuous integration runner caches virtual environments under the "
        "home directory and needs a manual purge after dependency upgrades",
        "phase": "migrate every surface onto typed outcomes so rendering lives in exactly one "
        "place and formats never drift between surfaces",
    }

    def add(kind, rid, t, answer=None):
        if kind == "lesson":
            return A.lesson_add(repo, A.LessonDraft(title=t, rule=t, id=rid, answer=answer))
        if kind == "decision":
            return A.decision_add(repo, Draft(title=t, decision=t, id=rid, answer=answer))
        if kind == "research":
            f = A.ResearchFinding(question=t, verdict="THEORETICAL", claim=t, id=rid, answer=answer)
            return A.research_add(repo, f)
        if kind == "memory":
            return A.memory_add(repo, t, id=rid, answer=answer)
        return A.phase_add(repo, rid, title=t, body=t, answer=answer)

    for kind, text in texts.items():
        assert add(kind, f"{kind}-1", text).exit == 0, kind
        more = text + " and more words besides"
        again = add(kind, f"{kind}-2", more)
        assert again.exit == 3, (kind, again.reason)
        assert again.data["candidates"][0]["id"] == f"{kind}-1"
        assert add(kind, f"{kind}-2", more, DD.Answer("new")).exit == 0, kind


def test_session_notes_and_prompts_are_never_checked(repo):
    run_cli(repo, "init")
    s = A.session_start(repo, agent="a")
    sid = s.data["id"] if "id" in s.data else s.data.get("session", "")
    text = "the same long note about the very same thing repeated again and again"
    assert A.session_note(repo, sid, text, agent="a").exit == 0
    assert A.session_note(repo, sid, text, agent="a").exit == 0


def test_the_check_is_callable_for_the_importer(filed):
    cfg = Config.load(filed)
    log, _cfg, st = A._load(filed, "a")
    rec = DD.Record(kind="bug", event_kind="bug.found", rid="Bimp", body=REPORT)
    assert DD.check_add(filed, log, cfg, st, rec).refusal.exit == 3
    off = DD.with_check(cfg, on_match="off")
    assert DD.check_add(filed, log, off, st, rec).refusal is None


def test_an_exact_recurrence_without_an_id_is_a_new_linked_bug(repo):
    """Auto ids differ per filing, so the recurrence of a FIXED bug is a new record linked
    to it, not a merge into the closed one."""
    run_cli(repo, "init")
    text = "the commit hook reads the lease globs and ignores the item globs after an update"
    first = A.bug_found(repo, summary=text, agent="a")
    EventLog(repo, "a").append("bug.fixed", first.data["id"], {"regression_test": "t::x"})
    again = A.bug_found(repo, summary=text, agent="a")
    assert again.exit == 0 and again.data["id"] != first.data["id"]
    assert again.data["holder"]["state"] == "fixed"
    assert state(repo).links[again.data["id"]].linked("duplicate_of") == {first.data["id"]}


def test_a_decision_is_extendable_until_superseded():
    from types import SimpleNamespace as NS

    def st(**kw):
        return NS(decisions={"D1": NS(**{"superseded_by": "", "status": "accepted", **kw})})

    for status in ("", "accepted", "proposed"):
        assert DD.extendable(st(status=status), "D1", "decision")
    assert not DD.extendable(st(status="superseded"), "D1", "decision")
    assert not DD.extendable(st(superseded_by="D2"), "D1", "decision")
