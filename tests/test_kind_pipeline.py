"""The kind registry: an item kind -> its gate pipeline, `[gates].kind_pipelines`."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.core.kinds import KINDS, kind_pipelines_problem
from ddflow.core.model import Item
from ddflow.services import workflow as WF
from ddflow.services.gates import load_gates, pipeline_for, pipelined, pipelines
from ddflow.services.gates.kinds import kind_pipeline, kind_pipelines


def _item(kind: str, **kw) -> Item:
    return Item(id="X", kind=kind, title="t", **kw)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_default_pipeline_matches_the_pre_registry_rule(kind):
    """With no override, a kind runs exactly what `pipeline_for` ran before the registry."""
    cfg = Config()
    old = cfg.gates.phase_pipeline if kind == "phase" else cfg.gates.task_pipeline
    assert pipeline_for(_item(kind), cfg) == list(old)


def test_unregistered_kind_runs_the_task_pipeline():
    cfg = Config()
    assert pipeline_for(_item("mystery"), cfg) == list(cfg.gates.task_pipeline)


def test_override_replaces_one_kind_only():
    cfg = Config()
    cfg.gates.kind_pipelines = {"doc": ["implement", "merge"]}
    assert pipeline_for(_item("doc"), cfg) == ["implement", "merge"]
    assert pipeline_for(_item("task"), cfg) == list(cfg.gates.task_pipeline)
    assert kind_pipelines(cfg)["doc"] == ["implement", "merge"]
    assert list(kind_pipelines(cfg)) == list(KINDS)


def test_promotion_still_wins_over_the_kind():
    cfg = Config()
    cfg.gates.kind_pipelines = {"task": ["implement", "merge"]}
    assert pipeline_for(_item("task", promote_to="prod"), cfg) == list(cfg.gates.promotion_pipeline)


def test_kind_pipeline_is_a_running_pipeline():
    cfg = Config()
    cfg.gates.kind_pipelines = {"doc": ["implement", "docs_only_gate"]}
    assert pipelines(cfg, running=True)["kind:doc"] == ["implement", "docs_only_gate"]
    assert "docs_only_gate" in pipelined(cfg, running=True)
    assert kind_pipeline(cfg, "doc") == ["implement", "docs_only_gate"]


def test_check_names_an_undefined_gate_in_a_kind_pipeline(tmp_path):
    cfg = Config()
    cfg.gates.kind_pipelines = {"doc": ["implement", "nosuch_gate"]}
    findings = WF.check(cfg, load_gates(tmp_path, cfg))
    hit = [f for f in findings if "nosuch_gate" in f.detail]
    assert hit and hit[0].subject == "gates.kind_pipelines.doc"


def test_workflow_view_shows_each_kind():
    cfg = Config()
    cfg.gates.kind_pipelines = {"research": ["research", "merge"]}
    assert WF.describe(__import__("pathlib").Path("/nonexistent"), cfg, {}).kind_pipelines[
        "research"
    ] == ["research", "merge"]


@pytest.mark.parametrize(
    "value,ok",
    [
        ({}, True),
        ({"doc": ["merge"]}, True),
        ({"docs": ["merge"]}, False),
        ({"doc": "merge"}, False),
        ({"doc": [1]}, False),
        ("doc", False),
    ],
)
def test_knob_check(value, ok):
    assert (kind_pipelines_problem(value) == "") is ok
