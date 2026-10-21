"""Operator grants (DX-operator-grants-recorded): an extra review round the operator
allowed is an EVENT a review checks, not a message an agent relays.

`ddflow grant add` is a person's act (refused under an agent identity, no MCP tool, like
`ddflow approve`); a `--force` review past `[review].max_rounds` consumes a grant and
records its id; a STANDING grant carries a predicate review evaluates mechanically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import review as RV
from ddflow.config import Config
from ddflow.core.model import Item, fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import grants as GR


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py")
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _add(repo, *extra, count="1", gate="critic"):
    return run_cli(
        repo, "grant", "add", "--item", "T1", "--gate", gate, "--kind", "review_round",
        "--count", count, "--reason", "operator said so", *extra,
    )  # fmt: skip


def _round(repo, grant_id="", status="REVIEWED"):
    """A recorded full review round of T1.critic, as `ddflow review` writes it."""
    log = EventLog(repo, "probe")
    ev = {"review_kind": "full", "status": status, "rounds": 1, "reviewed_head": "aaa"}
    if grant_id:
        ev["budget_grant"] = grant_id
    log.append("gate.failed", "T1", {"gate": "critic", "by": "m", "reason": "x", "evidence": ev})


# -- add / list ----------------------------------------------------------------------------


def test_add_records_a_grant_and_list_shows_who_when_used(proj):
    code, out, err = _add(proj)
    assert code == 0, (out, err)
    assert "G1" in out
    code, out, err = run_cli(proj, "--json", "grant", "list")
    assert code == 0, err
    rows = json.loads(out)["grants"]
    assert [r["id"] for r in rows] == ["G1"]
    r = rows[0]
    assert (r["item"], r["gate"], r["kind"], r["count"], r["used"]) == (
        "T1", "critic", "review_round", 1, 0,
    )  # fmt: skip
    assert r["by"] and r["at"] and r["reason"] == "operator said so"
    code, out, _e = run_cli(proj, "grant", "list")
    assert "G1" in out and "0 used" in out


def test_ids_are_sequential(proj):
    _add(proj)
    code, out, _e = _add(proj, gate="rubber_duck")
    assert code == 0 and "G2" in out


def test_unknown_kind_and_bad_count_are_refused(proj):
    code, out, err = run_cli(
        proj, "grant", "add", "--item", "T1", "--gate", "critic", "--kind", "bogus",
        "--count", "1", "--reason", "r",
    )  # fmt: skip
    assert code != 0 and "review_round" in out + err
    code, out, err = _add(proj, count="0")
    assert code != 0 and "count" in out + err
    code, out, err = _add(proj, count="99")
    assert code != 0 and "max_count" in out + err
    assert _state(proj).approvals.get("grant:G1") is None


def test_a_reason_is_required(proj):
    code, _out, _err = run_cli(
        proj, "grant", "add", "--item", "T1", "--gate", "critic", "--kind", "review_round",
        "--count", "1",
    )  # fmt: skip
    assert code != 0
    assert not [s for s in _state(proj).approvals if s.startswith("grant:")]


def test_unknown_item_is_refused(proj):
    code, out, err = run_cli(
        proj, "grant", "add", "--item", "NOPE", "--gate", "critic", "--kind", "review_round",
        "--count", "1", "--reason", "r",
    )  # fmt: skip
    assert code != 0 and "NOPE" in out + err
    assert not [s for s in _state(proj).approvals if s.startswith("grant:")]


# -- operator identity only ----------------------------------------------------------------


def test_an_agent_identity_cannot_add_a_grant(proj):
    code, out, err = run_cli(
        proj, "--agent", "bot", "grant", "add", "--item", "T1", "--gate", "critic",
        "--kind", "review_round", "--count", "1", "--reason", "r",
    )  # fmt: skip
    assert code == 3 and "agent identity" in out + err
    assert not [s for s in _state(proj).approvals if s.startswith("grant:")]


def test_there_is_no_mcp_tool_for_grants():
    from ddflow.surfaces.declared import grants as declared

    assert declared.COMMANDS and all(not c.tool for c in declared.COMMANDS)


# -- consume -------------------------------------------------------------------------------


def _cfg(**review):
    cfg = Config.load()
    for k, v in review.items():
        setattr(cfg.review, k, v)
    return cfg


def _grant(proj, count="1", *extra):
    code, out, err = _add(proj, *extra, count=count)
    assert code == 0, (out, err)
    return _state(proj)


def test_force_past_the_cap_with_a_grant_needs_no_free_text_and_cites_the_id(proj):
    st = _grant(proj)
    cfg = _cfg(max_rounds=1)
    g = GR.pick(st, "T1", "critic", force=True)
    assert g is not None and g.id == "G1"
    why, forced = RV._budget(cfg, "T1", "critic", 1, True, "", lambda _l: None, grant=g)
    assert why == "" and "G1" in forced


def test_without_a_grant_force_stays_as_before(proj):
    cfg = _cfg(max_rounds=1)
    why, forced = RV._budget(cfg, "T1", "critic", 1, True, "", lambda _l: None, grant=None)
    assert "--reason" in why and forced == ""
    why, forced = RV._budget(cfg, "T1", "critic", 1, True, "free text", lambda _l: None)
    assert why == "" and forced == "free text"


def test_a_grant_is_not_spent_without_force(proj):
    st = _grant(proj)
    assert GR.pick(st, "T1", "critic", force=False) is None


def test_a_grant_does_not_cross_gates_or_items(proj):
    _grant(proj)
    run_cli(proj, "task", "add", "T2", "--globs", "u.py")
    st = _state(proj)
    assert "T2" in st.items
    assert GR.pick(st, "T1", "rubber_duck", force=True) is None
    assert GR.pick(st, "T2", "critic", force=True) is None


def test_a_recorded_round_that_cites_the_grant_uses_it_up(proj):
    _grant(proj)
    _round(proj, "G1")
    st = _state(proj)
    (g,) = GR.grants(st)
    assert (g.used, g.remaining) == (1, 0)
    assert GR.pick(st, "T1", "critic", force=True) is None
    assert "1 used" in GR.lines(st, "T1", "critic")[0]


def test_a_count_of_two_allows_two_rounds(proj):
    _grant(proj, "2")
    _round(proj, "G1")
    st = _state(proj)
    assert GR.pick(st, "T1", "critic", force=True).id == "G1"
    _round(proj, "G1")
    assert GR.pick(_state(proj), "T1", "critic", force=True) is None


def test_an_expired_grant_is_not_picked(proj):
    st = _grant(proj)
    (g,) = GR.grants(st)
    later = GR.parse_expiry(g.expires) + 1
    assert GR.pick(st, "T1", "critic", force=True, now=later) is None
    assert "expired" in GR.lines(st, "T1", "critic", now=later)[0]


def test_ttl_zero_never_expires(proj, monkeypatch):
    monkeypatch.setenv("DDFLOW_GRANTS_TTL_H", "0")
    st = _grant(proj)
    (g,) = GR.grants(st)
    assert g.expires == ""
    assert GR.pick(st, "T1", "critic", force=True, now=9e12) is not None


# -- standing grants -----------------------------------------------------------------------


def test_a_standing_grant_needs_a_known_predicate(proj):
    code, out, err = _add(proj, "--when", "whenever")
    assert code != 0 and "tree_changed_after_triage" in out + err


def test_a_standing_grant_applies_mechanically_when_the_predicate_holds(proj):
    code, out, err = _add(proj, "--when", "tree_changed_after_triage", count="2")
    assert code == 0, (out, err)
    st, cfg = _state(proj), _cfg(max_rounds=1)
    held = GR.Ctx(untriaged=0, reviewed_head="aaa", head="bbb")
    g = GR.pick(st, "T1", "critic", force=False, ctx=held)
    assert g is not None and g.standing
    why, forced = RV._budget(cfg, "T1", "critic", 1, False, "", lambda _l: None, grant=g)
    assert why == "" and "G1" in forced


@pytest.mark.parametrize(
    "ctx",
    [
        GR.Ctx(untriaged=1, reviewed_head="aaa", head="bbb"),  # a finding still open
        GR.Ctx(untriaged=0, reviewed_head="aaa", head="aaa"),  # the tree did not change
        GR.Ctx(untriaged=0, reviewed_head="", head="bbb"),  # nothing reviewed before
        None,  # nothing known: not mechanically true, so not applied
    ],
)
def test_a_standing_grant_does_not_apply_when_the_predicate_fails(proj, ctx):
    assert _add(proj, "--when", "tree_changed_after_triage")[0] == 0
    st = _state(proj)
    assert GR.pick(st, "T1", "critic", force=False, ctx=ctx) is None


def test_a_standing_grant_is_capped_by_its_count(proj):
    assert _add(proj, "--when", "tree_changed_after_triage", count="1")[0] == 0
    _round(proj, "G1")
    held = GR.Ctx(untriaged=0, reviewed_head="aaa", head="bbb")
    assert GR.pick(_state(proj), "T1", "critic", False, ctx=held) is None


def test_kinds_are_extensible_through_the_registry(proj):
    assert "review_round" in GR.KINDS
    GR.register_kind(GR.GrantKind("extra_thing", "a test kind", whens={}))
    try:
        g = GR.add(
            EventLog(proj, "probe"), _cfg(), item="T1", gate="critic", kind="extra_thing",
            count=1, reason="r", gates={"critic"},
        )  # fmt: skip
        assert g.kind == "extra_thing" and GR.describe(g).startswith("grant G1: 1 ")
    finally:
        GR.KINDS.pop("extra_thing")


# -- shown ---------------------------------------------------------------------------------


def test_gate_status_shows_the_grant(proj):
    _add(proj)
    code, out, err = run_cli(proj, "gate", "status", "T1")
    assert code == 0, err
    assert "grant G1" in out and "1 extra round" in out and "0 used" in out


# -- through review's own scope --------------------------------------------------------------


def _scope(proj, **over):
    cfg = _cfg(max_rounds=1)
    log = EventLog(proj, "probe")
    st = fold(log.read_all(), strict=False)
    a = {
        "item": "T1", "gate": "critic", "chunks": None, "delta": False, "commit": "",
        "base": "", "branch": "", "called_from": None, "force": False, "reason": "",
        "full": False, **over,
    }  # fmt: skip
    said: list[str] = []
    got = RV._scope(proj, cfg, st, log, st.items["T1"], said.append, [], locals_=a)
    return a, got, said


def test_review_scope_cites_the_grant_and_says_so(proj):
    _grant(proj)
    _round(proj)  # the budget (1) is spent
    a, (_kind, _done, forced, _d, _h, why, _r), said = _scope(proj, force=True)
    assert why == "" and a["grant"] == "G1" and "G1" in forced
    assert any("grant G1" in line for line in said)


def test_review_scope_without_force_is_still_refused(proj):
    _grant(proj)
    _round(proj)
    a, got, _said = _scope(proj)
    assert got[5] and a["grant"] == ""


# -- review round 1 findings -----------------------------------------------------------------


def test_a_round_the_budget_allows_anyway_does_not_spend_a_grant(proj):
    """Finding 1: on_exceed = warn lets the round run with no grant, so none is consumed."""
    _grant(proj)
    _round(proj)
    cfg = _cfg(max_rounds=1, on_exceed="warn")
    log = EventLog(proj, "probe")
    st = fold(log.read_all(), strict=False)
    a = {
        "item": "T1", "gate": "critic", "chunks": None, "delta": False, "commit": "",
        "base": "", "branch": "", "called_from": None, "force": True, "reason": "", "full": False,
    }  # fmt: skip
    got = RV._scope(proj, cfg, st, log, st.items["T1"], lambda _l: None, [], locals_=a)
    assert a["grant"] == "" and got[2] == "" and got[5] == "", "the round runs, citing nothing"


def test_a_grant_for_a_gate_the_project_does_not_have_is_refused(proj):
    code, out, err = _add(proj, gate="no_such_gate")
    assert code == 1 and "no_such_gate" in out + err
    assert not [s for s in _state(proj).approvals if s.startswith("grant:")]


def test_the_oldest_usable_grant_is_taken_first_standing_or_not(proj):
    assert _add(proj)[0] == 0  # G1, plain
    assert _add(proj, "--when", "tree_changed_after_triage")[0] == 0  # G2, standing
    held = GR.Ctx(untriaged=0, reviewed_head="aaa", head="bbb")
    assert GR.pick(_state(proj), "T1", "critic", force=True, ctx=held).id == "G1"


def test_grants_order_by_their_number_not_their_text():
    def row(n):
        t = {"item": "T1", "gate": "critic", "kind": "review_round", "count": 1, "reason": "r"}
        return {"human": True, "user": "u", "at": "2026-01-01T00:00:00Z", "terms": t}

    st = fold([], strict=False)
    for n in (10, 2, 1):
        st.approvals[f"grant:G{n}"] = [row(n)]
    assert [g.id for g in GR.grants(st)] == ["G1", "G2", "G10"]


def test_the_agents_reason_is_kept_beside_the_grant(proj):
    st = _grant(proj)
    g = GR.pick(st, "T1", "critic", force=True)
    _why, forced = RV._budget(
        _cfg(max_rounds=1), "T1", "critic", 1, True, "flaky test", lambda _l: None, grant=g
    )
    assert "G1" in forced and "flaky test" in forced


def test_add_refuses_a_gate_outside_the_given_gates(proj):
    with pytest.raises(GR.GrantInvalid, match="no_such_gate"):
        GR.add(
            EventLog(proj, "probe"), _cfg(), item="T1", gate="no_such_gate",
            kind="review_round", count=1, reason="r", gates={"critic"},
        )  # fmt: skip
    with pytest.raises(TypeError):
        GR.add(  # type: ignore[call-arg]
            EventLog(proj, "probe"), _cfg(), item="T1", gate="critic",
            kind="review_round", count=1, reason="r",
        )  # fmt: skip


def test_a_grant_for_a_gate_gone_from_the_item_does_not_break_listing():
    t = {"item": "foo", "gate": "removed_gate", "kind": "review_round", "count": 1, "reason": "r"}
    st = fold([], strict=False)
    st.items["foo"] = Item(id="foo", kind="task")
    st.approvals["grant:G1"] = [
        {"human": True, "user": "u", "at": "2026-01-01T00:00:00Z", "terms": t}
    ]
    assert [(g.id, g.used) for g in GR.grants(st)] == [("G1", 0)]


def test_argument_errors_fail_and_only_the_agent_identity_is_refused(proj):
    code, _o, _e = _add(proj, count="0")
    assert code == 1, "a bad argument is a failure, not a coordination refusal"
    code, _o, _e = run_cli(
        proj, "--agent", "bot", "grant", "add", "--item", "T1", "--gate", "critic",
        "--kind", "review_round", "--count", "1", "--reason", "r",
    )  # fmt: skip
    assert code == 3


def test_the_grant_reaches_the_recorded_evidence():
    assert RV._cite_grant(None, "G1") == {"budget_grant": "G1"}
    assert RV._cite_grant({"x": 1}, "G1") == {"x": 1, "budget_grant": "G1"}
    assert RV._cite_grant({"x": 1}, "") == {"x": 1}
    assert RV._cite_grant(None, "") is None
