"""D-rule-dedupe-everywhere: a rule is compared with every record kind when it is added or
edited -- decisions, lessons, research, tasks -- with the same ask / extend / related
answers as every other add, on the CLI and over MCP. Before, a rule was compared only with
the other rules, so a rule restating a binding decision was filed without a word."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import rules as R
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

DECISION = (
    "every migration script runs inside one database transaction and is rolled back "
    "whole when any statement in it fails"
)
RESTATED = (
    "each migration script must run inside a single database transaction and be rolled "
    "back whole when any of its statements fails"
)
UNRELATED = "name every test after the behaviour it pins, never after the function it calls"


@pytest.fixture
def proj(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
    code, out, err = run_cli(
        repo, "decision", "add", "--id", "D-mig", "--title", DECISION, "--decision", DECISION
    )
    assert code == 0, (code, out, err)
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    return repo


def _rules(proj: Path) -> set[str]:
    return {r.id for r in R.RulesStorage(proj).list()}


def test_a_rule_restating_a_decision_is_refused(proj):
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-mig", "--title", "Migrations", "--content", RESTATED
    )
    assert code == 3, (code, out, err)
    assert "refused: possible duplicate" in err and "D-mig" in err
    assert "r-mig" not in _rules(proj), "a refusal files nothing"


def test_an_unrelated_rule_is_filed(proj):
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-names", "--title", "Test names", "--content", UNRELATED
    )
    assert code == 0, (code, out, err)
    assert "r-names" in _rules(proj)


def test_new_files_it(proj):
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-mig", "--title", "Migrations", "--content", RESTATED,
        "--new",
    )  # fmt: skip
    assert code == 0, (code, out, err)
    assert "r-mig" in _rules(proj)


def test_related_files_it_and_names_the_decision(proj):
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-mig", "--title", "Migrations", "--content", RESTATED,
        "--related", "D-mig",
    )  # fmt: skip
    assert code == 0, (code, out, err)
    assert "r-mig" in _rules(proj) and "related to D-mig" in out


def test_extends_puts_the_text_on_the_open_decision_and_files_no_rule(proj):
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-mig", "--title", "Migrations", "--content", RESTATED,
        "--extends", "D-mig",
    )  # fmt: skip
    assert code == 0, (code, out, err)
    assert "r-mig" not in _rules(proj)
    assert "D-mig" in out
    st = fold(EventLog(proj, "t").read_all(), strict=False)
    added = st.links["D-mig"].additions.values()
    assert any(RESTATED in (a.get("text") or "") for a in added), list(added)


def test_check_lists_the_decision_and_writes_nothing(proj):
    code, out, err = run_cli(
        proj, "--json", "rule", "add", "--id", "r-mig", "--title", "Migrations",
        "--content", RESTATED, "--check",
    )  # fmt: skip
    assert code == 0, (code, out, err)
    assert "D-mig" in out and "r-mig" not in _rules(proj)


def test_an_edit_that_restates_a_decision_is_refused_until_answered(proj):
    assert R.rule_add(proj, R.Rule(id="r-x", title="Test names", content=UNRELATED)).exit == 0
    code, out, err = run_cli(proj, "rule", "edit", "r-x", "--content", RESTATED)
    assert code == 3, (code, out, err)
    assert "D-mig" in err
    assert R.RulesStorage(proj).get("r-x").content == UNRELATED, "a refused edit changes nothing"
    code, out, err = run_cli(
        proj, "rule", "edit", "r-x", "--content", RESTATED, "--related", "D-mig"
    )
    assert code == 0, (code, out, err)
    assert R.RulesStorage(proj).get("r-x").content == RESTATED
    assert "related to D-mig" in out


def _mcp(proj: Path, name: str, args: dict) -> dict:
    srv = Server(proj, agent="m")
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return json.loads(r["result"]["content"][0]["text"])


def test_mcp_refuses_then_takes_the_answer(proj):
    args = {"id": "r-mig", "title": "Migrations", "content": RESTATED}
    body = _mcp(proj, "ddflow_rule_add", args)
    assert body.get("refusal"), body
    assert any(c["id"] == "D-mig" for c in body["candidates"])
    body = _mcp(proj, "ddflow_rule_add", {**args, "related": "D-mig"})
    assert body.get("related") == "D-mig", body
    assert "r-mig" in _rules(proj)


def test_mcp_edit_takes_new(proj):
    assert R.rule_add(proj, R.Rule(id="r-x", title="Test names", content=UNRELATED)).exit == 0
    body = _mcp(proj, "ddflow_rule_edit", {"id": "r-x", "content": RESTATED})
    assert body.get("refusal"), body
    body = _mcp(proj, "ddflow_rule_edit", {"id": "r-x", "content": RESTATED, "new": True})
    assert "refusal" not in body, body
    assert R.RulesStorage(proj).get("r-x").content == RESTATED


def test_edit_related_may_name_another_rule(proj):
    """roborev: the answer was validated against log records only, so a rule id read as
    'no such record'. As on `rule add`, naming a rule needs no cross-kind check."""
    assert R.rule_add(proj, R.Rule(id="r-a", title="Test names", content=UNRELATED)).exit == 0
    other = R.Rule(id="r-b", title="Lint", content="run the linter before every commit")
    assert R.rule_add(proj, other).exit == 0
    code, out, err = run_cli(proj, "rule", "edit", "r-a", "--content", "zz", "--related", "r-b")
    assert code == 0, (code, out, err)
    assert "related to r-b" in out


def test_a_check_that_could_not_run_says_the_rule_was_filed_unchecked(proj, monkeypatch):
    """roborev: an index that would not open filed the rule and reported plain success."""
    from types import SimpleNamespace

    from ddflow.surfaces.commands import rules as C

    def broken(*a, **k):
        raise OSError("index locked")

    monkeypatch.setattr("ddflow.infra.store.Store.ensure", broken)
    out = R.rule_add(proj, R.Rule(id="r-mig", title="Migrations", content=RESTATED))
    assert out.exit == 0 and "index locked" in out.data["dedupe_unavailable"]
    assert "filed UNCHECKED" in C._check_note(out)
    edit = R.rule_update(proj, "r-mig", content=UNRELATED)
    assert "index locked" in edit.data["dedupe_unavailable"]
    assert C._check_note(SimpleNamespace(data={})) == ""


def test_an_unknown_answer_is_refused_before_anything_is_filed(proj):
    out = R.rule_add(
        proj,
        R.Rule(id="r-q", title="Q", content=UNRELATED),
        dedup_answer=R.RuleDedupAnswer("maybe", ""),
    )
    assert out.exit == 1 and "unknown answer" in out.reason
    assert "r-q" not in _rules(proj)


def test_the_rules_limits_still_refuse(proj):
    """roborev: `_over_limits(...) or ...` read the (falsy) refusal as nothing."""
    from ddflow.config import Config

    cap = Config.load(proj).rules.max_size_bytes
    big = R.rule_add(proj, R.Rule(id="r-big", title="Big", content="x" * (cap + 1)))
    assert big.exit == 3 and "max_size_bytes" in big.reason
    assert "r-big" not in _rules(proj)


def test_an_edit_cannot_relate_a_rule_to_itself(proj):
    from types import SimpleNamespace

    from ddflow.surfaces.commands import rules as C

    assert R.rule_add(proj, R.Rule(id="r-a", title="Test names", content=UNRELATED)).exit == 0
    out = R.rule_update(proj, "r-a", content="zz", dedup_answer=R.RuleDedupAnswer("related", "r-a"))
    assert out.exit == 1 and "itself" in out.reason
    note = C._check_note(SimpleNamespace(data={"dedupe_unavailable": "x"}), "edited")
    assert note.strip().startswith("edited UNCHECKED")


def test_a_rule_duplicate_refusal_lists_the_other_kinds_too(proj):
    """Rubber-duck: a rule reading like another RULE and a decision was refused for the
    rule alone; `new` then filed it past the decision nobody was shown."""
    assert (
        R.rule_add(
            proj,
            R.Rule(id="r-old", title="Migrations", content=RESTATED),
            dedup_answer=R.RuleDedupAnswer("new", ""),
        ).exit
        == 0
    )
    out = R.rule_add(proj, R.Rule(id="r-new", title="Migrations", content=RESTATED))
    assert out.exit == 3
    ids = {c["id"] for c in out.data["candidates"]}
    assert {"r-old", "D-mig"} <= ids and "D-mig" in out.reason


def test_mcp_refuses_two_answers_at_once(proj):
    args = {"id": "r-mig", "title": "Migrations", "content": RESTATED}
    srv = Server(proj, agent="m")
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    r = srv.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "ddflow_rule_add", "arguments": {**args, "new": True, "related": "D-mig"}}}
    )  # fmt: skip
    assert r["result"]["isError"] and "not several" in r["result"]["content"][0]["text"]
    assert "r-mig" not in _rules(proj)


def test_an_answer_naming_a_rule_does_not_skip_the_other_kinds(proj):
    """Rubber-duck and critic: `related <rule>` relates two rules and says nothing about
    the decision the text restates; add and edit still check the other kinds."""
    other = R.Rule(id="r-lint", title="Lint", content="run the linter before every commit")
    assert R.rule_add(proj, other).exit == 0
    ans = R.RuleDedupAnswer("related", "r-lint")
    add = R.rule_add(proj, R.Rule(id="r-mig", title="M", content=RESTATED), dedup_answer=ans)
    assert add.exit == 3 and "D-mig" in add.reason
    assert R.rule_add(proj, R.Rule(id="r-x", title="Test names", content=UNRELATED)).exit == 0
    edit = R.rule_update(proj, "r-x", content=RESTATED, dedup_answer=ans)
    assert edit.exit == 3 and "D-mig" in edit.reason
    fine = R.rule_add(
        proj, R.Rule(id="r-y", title="Y", content="zebra quantum marmalade"), dedup_answer=ans
    )
    assert fine.exit == 0 and fine.data["related"] == "r-lint"
