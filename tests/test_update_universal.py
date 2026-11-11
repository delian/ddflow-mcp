"""`ddflow update` takes the same operations on every record kind (DX-update-universal)."""

from __future__ import annotations

import pytest

from ddflow import api
from ddflow.api import record_update as RU
from ddflow.core import outcome as O
from ddflow.core import record_fields as RF
from ddflow.core.model import fold
from ddflow.surfaces.declared.queue import COMMANDS, item_edit


def _state(log):
    return api.items._load(log.root if hasattr(log, "root") else log.repo, "agent-test")[2]


@pytest.fixture
def seeded(repo, log):
    log.append("bug.found", "B1", {"summary": "it breaks", "title": "breaks", "tags": ["x"]})
    log.append("lesson.recorded", "L1", {"title": "t", "rule": "r", "why": "w", "tags": []})
    log.append(
        "research.recorded",
        "R1",
        {"question": "q?", "claim": "c", "verdict": "THEORETICAL", "tags": []},
    )
    log.append("decision.recorded", "D1", {"title": "dt", "decision": "chose a", "tags": []})
    log.append("memory.recorded", "M1", {"text": "fact", "tags": []})
    return repo


def _st(repo):
    return api.items._load(repo, "agent-test")[2]


def _upd(repo, rid, **kw):
    return api.update(repo, rid, api.ItemEdit(**kw), agent="agent-test")


@pytest.mark.parametrize(
    ("rid", "attr", "book"),
    [
        ("B1", "summary", "bugs"),
        ("L1", "rule", "lessons"),
        ("R1", "claim", "research"),
        ("D1", "decision", "decisions"),
        ("M1", "text", "memories"),
    ],
)
def test_append_body_keeps_the_original(seeded, rid, attr, book):
    before = getattr(getattr(_st(seeded), book)[rid], attr)
    out = _upd(seeded, rid, append_body="a later note")
    assert out.exit == O.OK, out.reason
    after = getattr(getattr(_st(seeded), book)[rid], attr)
    assert after == before + "\n\n" + "a later note"


def test_bug_title_tags_globs_and_own_fields(seeded):
    out = _upd(seeded, "B1", title="new", add_tags=["y"], remove_tags=["x"], globs=["a/**"],
               set=["severity=high"])  # fmt: skip
    assert out.exit == O.OK, out.reason
    bug = _st(seeded).bugs["B1"]
    assert (bug.title, bug.tags, bug.globs, bug.severity) == ("new", ["y"], ["a/**"], "high")


def test_lesson_and_research_own_fields(seeded):
    assert _upd(seeded, "L1", set=["why=because", "seen_in=a,b"]).exit == O.OK
    les = _st(seeded).lessons["L1"]
    assert (les.why, les.seen_in) == ("because", ["a", "b"])
    assert _upd(seeded, "R1", set=["verdict=CONFIRMED"]).exit == O.OK
    assert _st(seeded).research["R1"].verdict == "CONFIRMED"


def test_set_items_carry_add_remove_over_mcp(seeded):
    out = _upd(seeded, "B1", set=["add_tags=p,q", "remove_tags=x"])
    assert out.exit == O.OK, out.reason
    assert _st(seeded).bugs["B1"].tags == ["p", "q"]


def test_refusal_names_kind_field_and_nearest_verb(seeded):
    out = _upd(seeded, "B1", needs=["T1"])
    assert out.exit == O.REFUSED
    for word in ("bug B1", "needs", "Nearest verb", "ddflow bug"):
        assert word in out.reason
    out = _upd(seeded, "L1", set=["nope=1"])
    assert out.exit == O.REFUSED and "lesson L1" in out.reason and "nope" in out.reason
    out = _upd(seeded, "B1", set=["severity=bogus"])
    assert "severity" in out.reason


def test_unchanged_is_nothing_and_unknown_id_is_not_found(seeded):
    assert _upd(seeded, "B1", title="breaks").exit == O.NOTHING
    out = _upd(seeded, "NOPE1", title="x")
    assert out.exit == O.FAIL and "no such item" in out.reason


def test_kind_prefix_resolves(seeded):
    assert _upd(seeded, "bug:B1", append_body="n").exit == O.OK


def test_replay_is_strict(seeded):
    _upd(seeded, "B1", append_body="n")
    # a field the catalogue does not list is a fold error, not silently dropped
    seeded_log = api.items._load(seeded, "agent-test")[0]
    seeded_log.append("bug.updated", "B1", {"fields": {"bogus": 1}})
    with pytest.raises(Exception, match="unknown field"):
        fold(seeded_log.read_all(), strict=True)
    assert fold(seeded_log.read_all(), strict=False).fold_problems


def test_session_and_memory_and_schedule_kinds(seeded, log):
    log.append("session.started", "S1", {"model": "m"})
    assert _upd(seeded, "S1", append_body="done").exit == O.OK
    assert _st(seeded).sessions["S1"].summary == "done"
    assert _upd(seeded, "M1", set=["source=x"]).exit == O.OK
    log.append(
        "schedule.defined", "J1", {"title": "job", "cadence": {"every": "1d"}, "prompt": "p"}
    )
    out = _upd(seeded, "J1", append_body="more", set=["jitter=3"])
    assert out.exit == O.OK, out.reason
    job = _st(seeded).schedules["J1"]
    assert job.prompt == "p\n\nmore" and job.jitter == 3


def test_every_kind_decides_every_operation():
    """Each operation is accepted by a kind or refused naming the kind, the field and a verb."""
    for name, kd in RF.KINDS.items():
        for op in RF.OPS:
            if op == "append_body":
                continue
            if op in kd.attrs:
                continue
            text = RF.refusal(kd, "X1", op)
            assert name in text and op.replace("_", "-") in text and "Nearest verb" in text, (
                name,
                op,
            )
    assert set(RF.ALL_KIND_NAMES) >= {"goal", "principle", "waiver", "rule", "trigger", "schedule"}


def test_a_rule_is_found_by_its_bare_id(repo):
    from ddflow.services.rules import Rule

    out = api.rule_add(
        repo,
        Rule(id="r-demo-rule", title="Demo rule", content="Always do the demo thing properly."),
        agent="agent-test",
    )
    assert out.exit == O.OK, out.reason
    out = _upd(repo, "r-demo-rule", append_body="Also this.")
    assert out.exit == O.OK, out.reason
    assert api.rule_get(repo, "r-demo-rule").data["content"].endswith("\n\nAlso this.")


def test_surfaces_expose_the_catalogue_operations():
    cmd = next(c for c in COMMANDS if c.path == ("update",))
    names = {p.name for p in cmd.params}
    for op in RF.OPS:
        assert op in names or op in ("parent",) or f"add_{op}" in names, op
    assert "set" in names
    assert item_edit({"set": ["a=b"]}).set == ["a=b"]
    assert RU.data_fields  # data-declared kinds derive their entry from the declaration


def test_mcp_twin_appends_to_a_bug_like_the_cli(seeded):
    """The MCP tool and the CLI build one ItemEdit (`item_edit`), so the same call on the same
    record gives the same result on both."""
    from ddflow.surfaces.registry import by_tool

    call = by_tool(COMMANDS)["ddflow_update"].call
    out = call(seeded, {"id": "B1", "append_body": "via mcp", "set": ["add_tags=m"]}, "agent-test")
    assert out.exit == O.OK, out.reason
    bug = _st(seeded).bugs["B1"]
    assert bug.summary.endswith("\n\nvia mcp") and "m" in bug.tags
    refused = call(seeded, {"id": "B1", "needs": "T1"}, "agent-test")
    assert refused.exit == O.REFUSED and "bug B1" in refused.reason


def test_a_missing_id_is_not_found_even_when_it_contains_several(seeded):
    out = _upd(seeded, "several", title="x")
    assert out.exit == O.FAIL and "no such item" in out.reason


def test_enum_value_whitespace_is_ignored(seeded):
    assert _upd(seeded, "B1", set=["severity=high "]).exit == O.OK


def test_set_on_a_task_carries_list_folds_and_refuses_other_names(repo):
    log = api.items._load(repo, "agent-test")[0]
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"title": "t", "parent": "P1", "tags": ["a"]})
    assert _upd(repo, "T1", set=["add_tags=b"]).exit == O.OK
    assert _st(repo).items["T1"].tags == ["a", "b"]
    out = _upd(repo, "T1", set=["why=x"])
    assert out.exit == O.REFUSED and "task T1" in out.reason
