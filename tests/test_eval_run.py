"""The evaluation runner over recorded fixtures (SE-eval-harness.3)."""

from __future__ import annotations

import ddflow.api.evalrun as ER
import ddflow.api.records as R
import ddflow.services.evalkit as K
from ddflow.config import Config

KIND = R.KINDS["evalfixture"]


def _add(repo, rid, workflow, planted, text="diff"):
    out = R.record_add(
        repo, KIND, rid, {"title": rid, "workflow": workflow, "planted": planted, "input": text}
    )
    assert out.exit == 0, out


def test_fixtures_are_read_back_by_workflow(repo):
    _add(repo, "f1", "wf-a", ["sql=injection"])
    _add(repo, "f2", "wf-b", [])
    assert [f["id"] for f in ER.fixtures(repo)] == ["f1", "f2"]
    only = ER.fixtures(repo, "wf-a")
    assert [f["id"] for f in only] == ["f1"] and only[0]["planted"] == ["sql=injection"]


def _may(repo, name, wf):
    row = ER.report(repo, {name: wf}).get(name)
    return (row["enabled"], row["why"]) if row else (False, "nothing was measured")


def test_a_workflow_that_catches_its_planted_set_may_be_enabled(repo):
    _add(repo, "f1", "wf", ["sql=injection"])
    agent = K.FakeAgent(["SQL injection in query"])
    ok, why = _may(repo, "wf", lambda f: [agent(f["input"])])
    assert ok and "caught its planted set" in why and agent.prompts == ["diff"]


def test_a_workflow_that_misses_one_may_not(repo):
    _add(repo, "f1", "wf", ["sql=injection", "race=lock"])
    ok, why = _may(repo, "wf", lambda f: ["injection here"])
    assert not ok and "1 of 2" in why


def test_the_thresholds_are_knobs(repo):
    cfg = Config()
    assert cfg.evaluation.max_miss_rate == 0.0 and cfg.evaluation.min_fixtures == 1
    _add(repo, "f1", "wf", ["a=x", "b=y"])
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[evaluation]\nmax_miss_rate = 0.5\n")
    assert _may(repo, "wf", lambda f: ["x"])[0]


def test_no_fixtures_means_not_enabled(repo):
    ok, why = _may(repo, "wf", lambda f: [])
    assert not ok and "nothing was measured" in why
