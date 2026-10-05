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


def test_workflow_drop_takes_a_gate_out_of_the_promotion_pipeline_too(repo):
    """`workflow drop` walked task and phase only: a gate only promotions ran was 'in
    neither pipeline; nothing to drop' (exit 2), and left in place."""
    run_cli(repo, "init")
    gate = '[gate.smoke]\ncommand = "true"'
    assert run_cli(repo, "config", "--append-toml", gate)[0] == 0
    code, out, err = run_cli(
        repo, "config", "--set", "gates.promotion_pipeline", '["unit_tests", "smoke", "merge"]'
    )
    assert code == 0, (out, err)
    code, out, err = run_cli(repo, "workflow", "drop", "smoke")
    assert code == 0, (out, err)
    assert Config.load(repo).gates.promotion_pipeline == ["unit_tests", "merge"]


def test_workflow_json_names_the_promotion_pipeline(repo):
    import json

    _repo_requiring_a_promotion_signoff(repo)
    data = json.loads(run_cli(repo, "--json", "workflow")[1])
    assert data["promotion_pipeline"] == ["unit_tests", "deploy_signoff", "merge"]


def test_with_no_environments_a_promotion_only_requirement_is_still_inert(repo):
    """roborev on 0a1cf8a5..1e4835c6: the promotion pipeline runs only where
    `flow.environments` exist. Without any, a gate only it names requires nothing, and
    must still be reported inert -- the class this check exists for."""
    run_cli(repo, "init")
    gate = '[gate.deploy_signoff]\nhuman = true\nprompt = "sign the deploy"'
    assert run_cli(repo, "config", "--append-toml", gate)[0] == 0
    code, out, err = run_cli(
        repo,
        "config",
        "--set",
        "gates.promotion_pipeline",
        '["unit_tests", "deploy_signoff", "merge"]',
    )
    assert code == 0, (out, err)
    required = sorted({*Config.load(repo).gates.required, "deploy_signoff"})
    code, out, err = run_cli(
        repo, "config", "--set", "gates.required", repr(required).replace("'", '"')
    )
    assert code != 0 and "deploy_signoff" in out + err, (code, out, err)
    cfg = Config.load(repo)
    cfg.gates.required = required
    assert G.inert_requirements(cfg) == ["deploy_signoff"]


def test_workflow_names_the_promotion_pipeline_only_where_it_runs(repo):
    """Bc0cd05d0c5: `ddflow workflow` printed 'A promotion passes through: ...' always,
    though without `flow.environments` no promotion exists and that pipeline never runs."""
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "workflow")
    assert code in (0, 1) and "A promotion passes through" not in out, out
    assert run_cli(repo, "config", "--set", "flow.environments", '["staging"]')[0] == 0
    out = run_cli(repo, "workflow")[1]
    assert "A promotion passes through: unit_tests, merge" in out, out
