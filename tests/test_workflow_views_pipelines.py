"""The workflow-state overview and the gate-coverage views see the promotion pipeline
(bug B726755d8f7).

`workflow_state` read the task and phase pipelines from raw config and drew its diagram
from the task pipeline alone; `companions` (`uncovered_gates`) and the MCP handshake
(`gate_gaps`) judged coverage over `task_pipeline` only. A gate that lives only in
`gates.promotion_pipeline` -- a deploy sign-off -- was invisible to all three, while
`ddflow workflow` (`workflow.describe`) already showed it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.api.workflow_state import workflow_state

SIGNOFF = "deploy_signoff"


def _with_promotion(repo: Path, *, environments: bool = True) -> None:
    run_cli(repo, "init")
    gate = f'[gate.{SIGNOFF}]\nhuman = true\nprompt = "sign the deploy"'
    assert run_cli(repo, "config", "--append-toml", gate)[0] == 0
    pairs = [("gates.promotion_pipeline", f'["unit_tests", "{SIGNOFF}", "merge"]')]
    if environments:
        pairs.append(("flow.environments", '["staging"]'))
    for key, value in pairs:
        code, out, err = run_cli(repo, "config", "--set", key, value)
        assert code == 0, (key, out, err)


def test_the_overview_names_the_promotion_pipeline_and_draws_it(repo):
    _with_promotion(repo)
    out = workflow_state(repo)
    assert out.data["workflow"]["promotion_pipeline"] == ["unit_tests", SIGNOFF, "merge"]
    assert SIGNOFF in out.data["workflow_diagram"], out.data["workflow_diagram"]


def test_a_promotion_only_gate_is_judged_for_coverage(repo):
    _with_promotion(repo)
    out = api.companions_list(repo, no_probe=True)
    assert SIGNOFF in out.data["gate_coverage"], sorted(out.data["gate_coverage"])
    assert SIGNOFF in out.data["uncovered_gates"]


def test_the_handshake_names_a_promotion_only_gap(repo):
    from ddflow.surfaces.mcp import _instruction_vars

    _with_promotion(repo)
    assert SIGNOFF in _instruction_vars(repo)["gate_gaps"]


def test_without_environments_no_promotion_runs_so_none_is_shown(repo):
    """As `ddflow workflow` says it (Bc0cd05d0c5): a pipeline nothing passes through
    advertises nothing."""
    _with_promotion(repo, environments=False)
    out = workflow_state(repo)
    assert out.data["workflow"]["promotion_pipeline"] == []
    assert SIGNOFF not in out.data["workflow_diagram"]
    assert SIGNOFF not in api.companions_list(repo, no_probe=True).data["gate_coverage"]
