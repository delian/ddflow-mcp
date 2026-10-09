"""Pipeline views, gate coverage and the config editor read ONE source
(B-uni-gate-record.6-views): `workflow.pipeline_lists`, `gate_gaps`, `similar_gates`
and `apply_gate_table`."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.api.workflow_state import workflow_state
from ddflow.config import Config
from ddflow.services import companions as CO
from ddflow.services import workflow as WF
from ddflow.services.gates import GateDef, load_gates


def _status(gates, usable):
    return SimpleNamespace(usable=usable, companion=SimpleNamespace(gates=gates))


def test_gate_gaps_leaves_a_gate_with_an_unknown_companion_unjudged():
    cover = {"rules": [], "critic": [], "ci": ["x"], "docs": []}
    statuses = [_status(["rules"], None), _status(["critic"], False)]
    assert CO.gate_gaps(cover, statuses) == ["critic", "docs"]


def test_companions_and_the_handshake_agree_on_the_gaps(repo):
    from ddflow.surfaces.mcp import _instruction_vars

    run_cli(repo, "init")
    out = api.companions_list(repo, no_probe=True)
    # With no probe a cli companion's install state is unknown, so neither view calls
    # its gate a gap: they are one list, not two definitions.
    assert out.data["uncovered_gates"] == _instruction_vars(repo)["gate_gaps"]
    assert "rules" not in out.data["uncovered_gates"]


def test_every_view_names_the_same_pipelines(repo):
    from ddflow.surfaces.mcp import _instruction_vars

    run_cli(repo, "init")
    cfg = Config.load(repo)
    task, phase, promotion = WF.pipeline_lists(cfg)
    wf = workflow_state(repo).data["workflow"]
    assert (wf["task_pipeline"], wf["phase_pipeline"], wf["promotion_pipeline"]) == (
        task,
        phase,
        promotion,
    )
    view = api.workflow_show(repo)
    assert view.data["task_pipeline"] == task and view.data["phase_pipeline"] == phase
    assert _instruction_vars(repo)["task_pipeline"] == task


def test_the_editor_and_check_suggest_the_same_gate(repo):
    run_cli(repo, "init")
    cfg = Config.load(repo)
    known = load_gates(repo, cfg)
    assert WF.similar_gates("unit_test", known)[0] == "unit_tests"
    out = api.workflow_pipeline(repo, "task", "unit_test,merge")
    assert out.exit != 0 and "did you mean 'unit_tests'" in out.reason


def test_apply_gate_table_creates_overlays_and_restricts():
    gates = {"a": GateDef(id="a", command="x")}
    WF.apply_gate_table(gates, {"a": {"command": "y", "bogus": 1}, "b": {"prompt": "p"}})
    assert gates["a"].command == "y" and gates["b"].prompt == "p"
    only = {"a": GateDef(id="a")}
    WF.apply_gate_table(
        only, {"a": {"human": True, "command": "z"}, "c": {"human": True}}, only=("human",)
    )
    assert only["a"].human is True and only["a"].command == "" and "c" not in only
