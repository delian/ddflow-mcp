"""B7f0b7c8839: `inert_requirements` (and the workflow checks beside it) knew only the task
and phase pipelines, so a gate that lives only in `gates.promotion_pipeline` -- a deploy
sign-off -- counted as "in neither pipeline": requiring it was refused, and a config
that required it anyway blocked EVERY item's completion as an inert requirement."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import completion
from ddflow.services import gates as G
from ddflow.services import workflow as W


def _repo_requiring_a_promotion_signoff(repo: Path) -> None:
    run_cli(repo, "init")
    gate = '[gate.deploy_signoff]\nhuman = true\nprompt = "sign the deploy"'
    assert run_cli(repo, "config", "--append-toml", gate)[0] == 0
    for key, value in (
        ("gates.promotion_pipeline", '["unit_tests", "deploy_signoff", "merge"]'),
        ("flow.environments", '["staging"]'),
    ):
        code, out, err = run_cli(repo, "config", "--set", key, value)
        assert code == 0, (key, out, err)
    required = sorted({*Config.load(repo).gates.required, "deploy_signoff"})
    code, out, err = run_cli(
        repo, "config", "--set", "gates.required", repr(required).replace("'", '"')
    )
    assert code == 0, f"requiring a promotion-only gate was refused: {out}{err}"
    assert run_cli(repo, "task", "add", "T1", "--title", "a task")[0] == 0


def test_a_gate_only_in_the_promotion_pipeline_can_be_required(repo):
    _repo_requiring_a_promotion_signoff(repo)
    cfg = Config.load(repo)
    assert "deploy_signoff" in cfg.gates.required
    assert G.inert_requirements(cfg) == []


def test_requiring_it_is_enforced_on_a_promotion_and_blocks_nothing_else(repo):
    _repo_requiring_a_promotion_signoff(repo)
    cfg = Config.load(repo)
    state = fold(EventLog(repo, "reader").read_all(), strict=False)
    task = completion.verdict(state, cfg, "T1", repo=repo)
    assert not any("no pipeline runs" in b for b in task.blockers), task.blockers
    assert not any("deploy_signoff" in b for b in task.blockers), task.blockers
    state.items["T1"].promote_to = "staging"  # the same item, as a promotion
    promo = completion.verdict(state, cfg, "T1", repo=repo)
    assert any("required gate(s) not passed" in b and "deploy_signoff" in b for b in promo.blockers)


def test_the_workflow_checks_see_the_promotion_pipeline(repo):
    _repo_requiring_a_promotion_signoff(repo)
    cfg = Config.load(repo)
    gates = G.load_gates(repo, cfg)
    findings = W.check(cfg, gates, repo)
    assert not [f for f in findings if f.subject == "gates.required"], findings
    view = W.describe(repo, cfg, gates)
    assert view.promotion_pipeline == ["unit_tests", "deploy_signoff", "merge"]
    signoff = [g for g in view.gates if g.id == "deploy_signoff"]
    assert (
        signoff and signoff[0].in_promotion and signoff[0].required and signoff[0].kind == "human"
    )
