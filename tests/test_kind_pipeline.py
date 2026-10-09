"""The kind registry: an item kind -> its gate pipeline, `[gates].kind_pipelines`."""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.config_sections._kinds import KINDS, kind_pipelines_problem
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


def test_default_config_adds_no_kind_entries_to_pipelines(tmp_path):
    """The built-in kinds are not `kind:` pipelines: only `[gates].kind_pipelines` entries
    are, so an untouched project reports each pipeline's problems once, as before."""
    cfg = Config()
    assert not [k for k in pipelines(cfg) if k.startswith("kind:")]
    assert not [
        f for f in WF.check(cfg, load_gates(tmp_path, cfg)) if "kind_pipelines" in f.subject
    ]


def test_drop_writes_kind_pipelines_only_when_one_is_configured(repo):
    """`workflow drop` edits a kind's own pipeline and leaves an untouched project's
    `kind_pipelines` unwritten (the built-in kinds are not overrides)."""
    from conftest import run_cli

    assert run_cli(repo, "workflow", "drop", "dedupe")[0] == 0
    assert "kind_pipelines" not in (repo / ".ddflow" / "config.toml").read_text()
    assert (
        run_cli(
            repo,
            "config",
            "--set",
            "gates.kind_pipelines",
            '{ doc = ["implement", "bug_hunt", "merge"] }',
        )[0]
        == 0
    )
    assert run_cli(repo, "workflow", "drop", "bug_hunt")[0] == 0
    data = json.loads(run_cli(repo, "--json", "workflow")[1])
    assert data["kind_pipelines"]["doc"] == ["implement", "merge"]
    assert (
        run_cli(repo, "config", "--set", "gates.kind_pipelines", '{ docs = ["merge"] }')[0] != 0
    ), "an unregistered kind is refused"


def test_check_flags_a_phase_only_gate_in_a_kind_pipeline(tmp_path):
    """`applies_to` is judged for kind pipelines too: a document is task-like."""
    cfg = Config()
    gates = load_gates(tmp_path, cfg)
    gates["tasks"].applies_to = "phase"
    cfg.gates.kind_pipelines = {"doc": ["implement", "tasks", "merge"]}
    hit = [f for f in WF.check(cfg, gates) if f.subject == "tasks" and "applies_to" in f.detail]
    assert hit and "doc pipeline" in hit[0].detail


# ---- applies_when: a gate that runs only on work touching its paths -------------------


def _gate(**kw):
    from ddflow.services.gates import GateDef

    return GateDef(id="g", **kw)


@pytest.mark.parametrize(
    "when,globs,applies",
    [
        ([], ["src/a.py"], True),  # no patterns: always
        (["docs/**"], [], True),  # no declared globs: cannot judge, so it applies
        (["docs/**"], ["docs/guide.md"], True),
        (["docs/**"], ["src/a.py"], False),
        (["docs/**", "api/**"], ["src/a.py", "api/x.py"], True),
        (["docs/**"], ["src/**", "tests/"], False),
    ],
)
def test_gate_applies_grid(when, globs, applies):
    from ddflow.services.gates import gate_applies

    item = _item("task", globs=globs)
    assert (gate_applies(_gate(applies_when=when), item) == "") is applies
    if not applies:
        assert "docs/**" in gate_applies(_gate(applies_when=when), item)


def test_an_undefined_gate_always_applies():
    from ddflow.services.gates import gate_applies

    assert gate_applies(None, _item("task", globs=["a.py"])) == ""


def test_status_without_definitions_or_patterns_is_unchanged(repo):
    """Passing the gate definitions changes nothing while no gate has `applies_when`."""
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import gates as G

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    cfg = Config.load(repo)
    st = fold(EventLog(repo).read_all())
    plain = G.status(st, cfg, "T1")
    withdefs = G.status(st, cfg, "T1", G.load_gates(repo, cfg))
    assert withdefs == plain
    assert withdefs.not_applicable == {}


def test_a_gate_outside_the_declared_work_is_reported_and_waited_on_by_nothing(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    assert run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["docs/**"]')[0] == 0
    _code, out, err = run_cli(repo, "gate", "status", "T1")
    assert "[-] dedupe  -- not applicable: applies only to work touching docs/**" in out, out + err
    # nothing waits on it: it is not silent, so completion does not ask for an outcome
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import completion as CM
    from ddflow.services import gates as G

    cfg = Config.load(repo)
    st = fold(EventLog(repo).read_all())
    s = G.status(st, cfg, "T1", G.load_gates(repo, cfg))
    assert "dedupe" in s.not_applicable and "dedupe" not in s.silent
    assert "dedupe" not in s.render().split("[-]")[0]
    v = CM.verdict(st, cfg, "T1", repo=repo)
    assert not any("dedupe" in b for b in v.blockers), v.blockers
    # and it is not "ahead" of the gate after it
    assert "dedupe" not in G.gates_ahead_of(st, cfg, "T1", "merge", G.load_gates(repo, cfg))
    assert "dedupe" in G.gates_ahead_of(st, cfg, "T1", "merge")


def test_a_gate_in_scope_is_waited_on_as_before(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/guide.md")
    run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["docs/**"]')
    out = run_cli(repo, "gate", "status", "T1")[1]
    assert "not applicable" not in out and "[ ] dedupe" in out, out


def test_brief_and_verify_leave_out_a_gate_that_does_not_apply(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["docs/**"]')
    run_cli(repo, "config", "--set", "gate.bug_hunt.applies_when", '["src/**"]')
    out = run_cli(repo, "brief", "--item", "T1")[1]
    remaining = next(ln for ln in out.splitlines() if "gates remaining" in ln)
    assert "dedupe" not in remaining and "bug_hunt" in remaining, remaining


def test_a_gate_with_a_recorded_outcome_always_applies(repo):
    """Work already done is never hidden: a failed outcome keeps its gate in play even
    where `applies_when` no longer covers the item, and it is not reported as N/A."""
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import gates as G

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "gate", "record", "T1", "dedupe", "--outcome", "failed", "--reason", "x")
    run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["docs/**"]')
    cfg = Config.load(repo)
    s = G.status(fold(EventLog(repo).read_all()), cfg, "T1", G.load_gates(repo, cfg))
    assert "dedupe" not in s.not_applicable and s.blocked_by == ["dedupe"]


def test_complete_and_verify_do_not_wait_on_a_gate_that_does_not_apply(repo):
    """Every applicable gate carries an outcome; the one outside the work has none and
    owes none: the status is complete and `verify` finds no silent gate."""
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import gates as G

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["docs/**"]')
    cfg = Config.load(repo)
    for gate in cfg.gates.task_pipeline:
        if gate != "dedupe":
            run_cli(repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "test")
    s = G.status(fold(EventLog(repo).read_all()), cfg, "T1", G.load_gates(repo, cfg))
    assert s.complete and s.silent == [] and list(s.not_applicable) == ["dedupe"]
    assert run_cli(repo, "complete", "T1", "--force", "--reason", "test")[0] == 0
    code, out, err = run_cli(repo, "verify", "T1")
    assert code in (0, 1), out + err  # a completed item: verified or flagged, not "not completed"
    assert "never run and never skipped" not in out, out
    from ddflow.services import completion as CM

    st = fold(EventLog(repo).read_all())
    assert not [b for b in CM.verdict(st, cfg, "T1", repo=repo).blockers if "dedupe" in b]


def test_workflow_text_lists_only_the_kinds_that_differ(repo):
    run_cli(repo, "init")
    default = run_cli(repo, "workflow")[1]
    assert "A doc passes through" not in default
    # the built-in phase line is printed once, by the phase pipeline itself
    assert default.count("A phase passes through") == 1, default
    assert (
        run_cli(
            repo, "config", "--set", "gates.kind_pipelines", '{ doc = ["implement", "merge"] }'
        )[0]
        == 0
    )
    out = run_cli(repo, "workflow")[1]
    assert "A doc passes through: implement, merge" in out, out
    assert "A bug passes through" not in out and out.count("A phase passes through") == 1


def test_a_phase_override_replaces_the_phase_line_instead_of_adding_one(repo):
    run_cli(repo, "init")
    assert (
        run_cli(
            repo, "config", "--set", "gates.kind_pipelines", '{ phase = ["research", "merge"] }'
        )[0]
        == 0
    )
    out = run_cli(repo, "workflow")[1]
    assert out.count("A phase passes through") == 1, out
    assert "A phase passes through: research, merge" in out, out


# ---- requires_evidence: a bare pass is refused where the evidence names none ----------


def _owner(table):
    return table.get


@pytest.mark.parametrize(
    "gate,evidence,owners,ok",
    [
        ({}, {}, None, True),  # nothing required: nothing asked
        ({"requires_evidence": ["report"]}, {}, None, False),
        ({"requires_evidence": ["report"]}, {"output_digest": "abc"}, None, True),
        ({"requires_evidence": ["report"]}, {"report_digest": "abc"}, None, True),
        ({"requires_evidence": ["report"]}, {"output_digest": "  "}, None, False),
        (
            {"requires_evidence": ["report"]},
            {"output_digest": "e3b0", "output_bytes": 0},
            None,
            False,
        ),
        (
            {"requires_evidence": ["report"]},
            {"output_digest": "ab", "output_bytes": 12},
            None,
            True,
        ),
        (
            {"requires_evidence": ["report"]},
            {"output_digest": "ab", "output_bytes": 1, "tail": "\n"},
            None,
            False,
        ),
        (
            {"requires_evidence": ["report"]},
            {"output_digest": "ab", "output_bytes": 9000, "tail": "\n\n"},
            None,
            True,  # the tail is not the whole output: something came before it
        ),
        ({"requires_evidence": ["link"]}, {"link": "R1"}, {"R1": "X"}, True),
        ({"requires_evidence": ["link"]}, {"link": ["R2", "R1"]}, {"R1": "X"}, True),
        ({"requires_evidence": ["link"]}, {"link": "R1"}, {"R1": "OTHER"}, False),
        ({"requires_evidence": ["link"]}, {"link": "R9"}, {"R1": "X"}, False),
        ({"requires_evidence": ["link"]}, {}, {"R1": "X"}, False),
        ({"requires_evidence": ["link"]}, {"link": "R1"}, None, False),  # cannot be resolved
        ({"evidence_fields": ["intent"]}, {"fields": {"intent": "make it so"}}, None, True),
        ({"evidence_fields": ["intent"]}, {"intent": "make it so"}, None, True),
        ({"evidence_fields": ["intent"]}, {"fields": {"intent": "LGTM"}}, None, False),
        ({"evidence_fields": ["intent"]}, {"fields": {"intent": "  "}}, None, False),
        ({"evidence_fields": ["intent"]}, {}, None, False),
    ],
)
def test_evidence_problems_grid(gate, evidence, owners, ok):
    from ddflow.services.gates import evidence_problems

    owner = None if owners is None else _owner(owners)
    assert (evidence_problems(_gate(**gate), evidence, "X", owner) == []) is ok


def test_every_missing_field_is_listed_at_once():
    from ddflow.services.gates import evidence_problems

    gate = _gate(evidence_fields=["intent", "edge cases", "verification"])
    (why,) = evidence_problems(gate, {"fields": {"intent": "x"}}, "X")
    assert "edge cases, verification" in why and "intent" not in why


def test_an_unknown_form_is_reported_not_ignored():
    from ddflow.services.gates import evidence_problems

    (why,) = evidence_problems(_gate(requires_evidence=["reprot"]), {}, "X")
    assert "reprot" in why and "report, link, fields" in why


def test_only_a_pass_is_held_to_the_requirement(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "config", "--set", "gate.dedupe.requires_evidence", '["report"]')
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "dedupe", "--outcome", "passed", "--evidence", "looked"
    )
    assert code != 0 and "cannot pass without a report" in out + err, out + err
    assert run_cli(repo, "gate", "skip", "T1", "dedupe", "--reason", "n/a here")[0] == 0
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "dedupe",
        "--outcome",
        "unavailable",
        "--reason",
        "tool down",
    )
    assert code == 0, out + err


def test_a_report_attached_as_output_passes(repo, tmp_path):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "config", "--set", "gate.dedupe.requires_evidence", '["report"]')
    report = tmp_path / "report.txt"
    report.write_text("0 findings\n")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "dedupe",
        "--outcome",
        "passed",
        "--evidence",
        "checked",
        "--output-file",
        str(report),
    )
    assert code == 0, out + err


def test_a_record_linked_to_another_item_does_not_satisfy_a_link(repo):
    """The `link` form resolves through the research notes of the folded state."""
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services.gates import evidence_problems, record_owner

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--title", "u", "--globs", "src/b.py")
    run_cli(
        repo, "research", "--item", "T1", "--question", "q", "--claim", "c", "--verdict",
        "THEORETICAL", "--probe", "none possible", "--new",
    )  # fmt: skip
    st = fold(EventLog(repo).read_all())
    (rid,) = list(st.research)
    owner = record_owner(st)
    gate = _gate(requires_evidence=["link"])
    assert evidence_problems(gate, {"link": rid}, "T1", owner) == []
    assert evidence_problems(gate, {"link": rid}, "T2", owner) != []
