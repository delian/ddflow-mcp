"""`ddflow workflow` — the rules in force here, and changing them safely.

Nothing joined the pipeline, the gates, the reviewers and the completion rules into one
answer. `gate status <id>` showed one item's position, `config --explain` printed ~60
flat knobs, and the only place that ever said "this project's pipeline is X" was the MCP
handshake — computed once at connect and unreachable from a terminal.

And nothing checked whether the pipeline made sense. The worst case is silent and
permanent: a pipeline naming a gate id that has no definition. The outcome folds to
empty, `require_outcome` refuses the completion forever, and `gate record` rejects the
id as unknown — so the item cannot be completed at all, and nothing says why. One typo
bricks every item that enters the pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.services.gates import load_gates
from ddflow.services.workflow import ADVISORY, PROBLEM, check

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _cfg(repo: Path, **over) -> Config:
    cfg = Config.load(repo)
    for k, v in over.items():
        setattr(cfg.gates, k, v)
    return cfg


def _conf(repo: Path) -> str:
    p = repo / ".ddflow" / "config.toml"
    return p.read_text() if p.exists() else ""


# -- it says what the rules are --------------------------------------------------------


def test_it_shows_the_pipeline_with_what_each_gate_demands(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "workflow")
    assert code == OK, out + err
    assert "unit_tests" in out and "merge" in out
    assert "required" in out, "which gates block completion is the first thing to know"
    assert "different-family reviewer" in out, out
    assert "[default]" in out, "a reader must be able to tell a choice from a default"


def test_the_json_surface_carries_the_same_verdict(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "--json", "workflow")
    data = json.loads(out)
    assert data["coherent"] is (code == OK)
    assert data["task_pipeline"], data
    assert {g["id"] for g in data["gates"]} >= set(data["task_pipeline"])


def test_a_command_gate_shows_whether_anything_has_proven_it_can_fail(repo):
    """A gate that cannot go red reports success on every change."""
    run_cli(repo, "init")
    run_cli(repo, "workflow", "gate", "lint", "--command", "ruff check .", "--into", "task")
    _code, out, _ = run_cli(repo, "workflow")
    assert "NOT proven able to fail" in out, out


# -- it says what does not hang together -----------------------------------------------


def test_a_pipeline_naming_an_undefined_gate_is_a_problem(repo):
    """The silent, permanent one. Every item entering the pipeline blocks on an outcome
    that can never be recorded, and before this nothing anywhere said so."""
    run_cli(repo, "init")
    cfg = _cfg(repo, task_pipeline=["research", "implment", "merge"])
    findings = check(cfg, load_gates(repo, cfg))
    bad = [f for f in findings if f.level == PROBLEM and "implment" in f.detail]
    assert bad, findings
    assert "implement" in bad[0].detail, "it must name the near miss"
    assert "forever" in bad[0].detail, "and say what it costs"


def test_a_required_gate_in_no_pipeline_is_a_problem(repo):
    run_cli(repo, "init")
    cfg = _cfg(repo, required=["critic"], task_pipeline=["implement"], phase_pipeline=["merge"])
    problems = [f for f in check(cfg, load_gates(repo, cfg)) if f.level == PROBLEM]
    assert any("critic" in f.detail for f in problems), problems


def test_a_gate_with_neither_command_nor_prompt_is_a_problem(repo):
    """It tells an agent nothing and gives a reviewer no contract."""
    run_cli(repo, "init")
    cfg = Config.load(repo)
    gates = load_gates(repo, cfg)
    gates["research"].prompt = ""
    gates["research"].command = ""
    problems = [f for f in check(cfg, gates) if f.level == PROBLEM]
    assert any(f.subject == "research" for f in problems), problems


def test_an_unproven_command_gate_is_advisory_not_a_problem(repo):
    """Not every project has got to `gate verify` yet. Reporting that as a defect makes
    the report something people learn to ignore."""
    run_cli(repo, "init")
    cfg = Config.load(repo)
    gates = load_gates(repo, cfg)
    gates["unit_tests"].command = "pytest -q"
    gates["unit_tests"].mutations = []
    found = [f for f in check(cfg, gates) if f.subject == "unit_tests"]
    assert found and found[0].level == ADVISORY, found


def test_doctor_reports_the_incoherence_too(repo):
    """It belongs in the command an operator runs when something is wrong, not only in
    the one they run when they already suspect the workflow."""
    run_cli(repo, "init")
    # Written directly: the editor now REFUSES this state, which is the point of the
    # test above. `doctor` still has to diagnose a config that got there another way —
    # a hand edit, a merge, a config written before the check existed.
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["research", "implment", "merge"]\nrequired = []\n'
    )
    code, out, _ = run_cli(repo, "doctor")
    assert code == FAIL
    assert "implment" in out, out
    assert "[problem]" not in out, "doctor prefixes its own severity; two reads as a bug"


# -- changing it, safely ---------------------------------------------------------------


def test_a_pipeline_with_an_undefined_gate_is_refused_before_anything_is_written(repo):
    run_cli(repo, "init")
    before = _conf(repo)
    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "research,implment,merge")
    assert code == FAIL
    assert "implement" in err, "name the near miss"
    assert _conf(repo) == before, "a refused edit must leave the file byte-identical"


def test_defining_a_gate_and_piping_it_is_one_validated_write(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo,
        "workflow",
        "gate",
        "lint",
        "--command",
        "ruff check .",
        "--into",
        "task",
        "--after",
        "implement",
        "--required",
    )
    assert code == OK, out + err
    _code, out, _ = run_cli(repo, "--json", "workflow")
    data = json.loads(out)
    pipeline = data["task_pipeline"]
    assert pipeline[pipeline.index("implement") + 1] == "lint", pipeline
    lint = next(g for g in data["gates"] if g["id"] == "lint")
    assert lint["kind"] == "command" and lint["required"] is True
    assert data["coherent"] is True, data["findings"]


def test_a_gate_with_no_command_and_no_prompt_cannot_be_piped(repo):
    """Otherwise the edit that is supposed to make the workflow better creates the
    exact permanent block this module exists to catch."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "workflow", "gate", "ghost", "--into", "task")
    assert code == FAIL
    assert "neither a command nor a prompt" in err, err


def test_after_an_id_that_is_not_in_the_pipeline_is_refused(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(
        repo, "workflow", "gate", "lint", "--command", "x", "--into", "task", "--after", "nope"
    )
    assert code == FAIL
    assert "nope" in err, err


def test_dropping_a_gate_also_drops_it_from_required(repo):
    """A required gate in no pipeline requires nothing — it is enforced by intersecting
    with the pipeline, so dropping one without the other manufactures a vacuous rule."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "workflow", "drop", "unit_tests")
    assert code == OK, out + err
    _code, out, _ = run_cli(repo, "--json", "workflow")
    data = json.loads(out)
    assert "unit_tests" not in data["task_pipeline"]
    assert "unit_tests" not in data["rules"]["gates.required"]["value"]
    assert data["coherent"] is True, data["findings"]


def test_dropping_a_gate_that_is_in_no_pipeline_says_so(repo):
    run_cli(repo, "init")
    run_cli(repo, "workflow", "drop", "unit_tests")
    code, _out, err = run_cli(repo, "workflow", "drop", "unit_tests")
    assert code == NOTHING, err


def test_dry_run_writes_nothing(repo):
    run_cli(repo, "init")
    before = _conf(repo)
    code, out, _ = run_cli(repo, "workflow", "pipeline", "task", "implement,merge", "--dry-run")
    assert code == OK, out
    assert "would set" in out, out
    assert _conf(repo) == before


# -- the hole every one of those writes would have gone through ------------------------


def test_config_set_refuses_a_section_that_does_not_exist(repo):
    """`[gatez]` is perfectly valid TOML. The write path validated the merged text for
    SYNTAX and then validated the config already on DISK — so this was written, and
    every later command failed to load the file. A writer that validates the state it is
    replacing has checked nothing."""
    run_cli(repo, "init")
    before = _conf(repo)
    code, _out, err = run_cli(repo, "config", "--set", "gatez.thing", "1")
    assert code == FAIL
    assert "Did you mean [gates]" in err, err
    assert _conf(repo) == before


def test_config_set_refuses_a_knob_that_does_not_exist(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "config", "--set", "gates.nope", "1")
    assert code == FAIL
    assert "unknown knob" in err, err


def test_append_toml_refuses_a_section_that_does_not_exist(repo):
    run_cli(repo, "init")
    before = _conf(repo)
    code, _out, err = run_cli(repo, "config", "--append-toml", "[gatez]\nx = 1")
    assert code == FAIL
    assert "gatez" in err, err
    assert _conf(repo) == before


def test_a_rejected_edit_never_leaves_the_config_empty(repo):
    """Writes go through a temp file and one rename. A plain `write_text` truncates
    first, and interrupted between truncate and write it leaves an EMPTY config — which
    loads as "no overrides at all" rather than as an error, silently reverting every
    knob."""
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gates.enforce_order", "block")
    run_cli(repo, "config", "--set", "gates.nope", "1")
    assert "enforce_order" in _conf(repo)
    assert Config.load(repo).gates.enforce_order == "block"


# -- both surfaces ---------------------------------------------------------------------


def test_every_workflow_command_is_reachable_over_mcp(repo):
    from ddflow.surfaces.mcp import TOOLS

    for tool in (
        "ddflow_workflow",
        "ddflow_workflow_pipeline",
        "ddflow_workflow_gate",
        "ddflow_workflow_drop",
    ):
        assert tool in TOOLS, sorted(t for t in TOOLS if "workflow" in t)
    assert TOOLS["ddflow_workflow"]["argv"]({}) == ["--json", "workflow"]
    argv = TOOLS["ddflow_workflow_gate"]["argv"](
        {"id": "lint", "command": "x", "into": "task", "required": True, "dry_run": True}
    )
    assert "--required" in argv and "--dry-run" in argv and "--into" in argv, argv


def test_the_mutating_tools_tell_the_agent_to_ask_first(repo):
    """A pipeline governs every future item, not the one in hand. An agent that edits it
    unilaterally has changed the rules the operator set."""
    from ddflow.surfaces.mcp import TOOLS

    for tool in ("ddflow_workflow_pipeline", "ddflow_workflow_gate", "ddflow_workflow_drop"):
        desc = TOOLS[tool]["description"]
        assert "operator" in desc, f"{tool} never mentions the operator"
        assert "WRITES" in desc, f"{tool} does not say it writes"


# -- the TOML line editor, on files humans actually write -------------------------------


def test_a_pipeline_written_across_several_lines_can_still_be_changed(repo):
    """`_toml_upsert` replaced ONE line. A ten-element pipeline is written across
    several — which is how a person writes it, and how `--append-toml` of a long list
    lands — so the replacement left the continuation lines orphaned:

        task_pipeline = ["x","y"]
          "a",
          "b",
        ]

    `Config.check` refuses that, so the file is never corrupted. But it means every
    workflow edit fails, permanently, with a TOML parse error pointing at a line the
    operator did not write.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = [\n  "research",\n  "implement",\n  "merge",\n]\n'
        "require_outcome = true\n"
    )
    code, out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,merge")
    assert code == OK, f"exit {code}\n{out}\n{err}"
    assert Config.load(repo).gates.task_pipeline == ["implement", "merge"]
    assert Config.load(repo).gates.require_outcome is True, "the neighbouring key survives"


def test_a_section_header_with_a_trailing_comment_is_still_found(repo):
    """`[gates]  # the pipeline` did not match `ln.strip() == "[gates]"`, so a SECOND
    `[gates]` was appended — and TOML forbids declaring a table twice."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[gates]  # how work is checked\nrequire_outcome = true\n"
    )
    code, _out, err = run_cli(repo, "config", "--set", "gates.require_outcome", "false")
    assert code == OK, err
    assert Config.load(repo).gates.require_outcome is False
    assert (repo / ".ddflow" / "config.toml").read_text().count("[gates]") == 1


MULTILINE_GATE = '''[gate.design_review]
prompt = """
Record the gate you ran, in this form:
command = <what you actually ran>
result  = pass | fail
Watch for [1] a new module, and [2] a new dependency.
"""
'''


def test_an_edit_never_reaches_inside_a_multi_line_string(repo):
    """The worst thing this line editor could do, and it did it.

    A hand-written agent prompt is a `\"\"\"...\"\"\"` value, and a line inside it that
    happens to read `command = ...` matched the key scan. So `--command "ruff check ."`
    was written INTO the prompt, the real `command` key was never set, the gate was
    added to the pipeline anyway, and `ddflow workflow` certified the result coherent.
    Exit 0. Every future task would then be handed a tampered instruction.

    A `[` inside the prose is the same bug by the other route: the section scan stopped
    at it and inserted the new key into the middle of the prompt.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(MULTILINE_GATE)
    code, out, err = run_cli(
        repo, "workflow", "gate", "design_review", "--command", "ruff check .", "--into", "task"
    )
    assert code == OK, out + err

    from ddflow.services.gates import load_gates

    cfg = Config.load(repo)
    g = load_gates(repo, cfg)["design_review"]
    assert g.command == "ruff check .", f"the flag was never written: {g.command!r}"
    assert "ruff check ." not in g.prompt, f"the prompt was rewritten:\n{g.prompt}"
    assert "Record the gate you ran" in g.prompt, "the prompt was mangled"


def test_a_required_gate_cannot_be_created_outside_every_pipeline(repo):
    """The writer manufacturing the exact problem its own reader reports.

    `--required` without `--into` exits 0 and leaves `ddflow workflow` exiting 1 with
    "required but is in neither pipeline". `drop` is careful about this class; `gate`
    was not — `_write_config` validated the SCHEMA and never the workflow.
    """
    run_cli(repo, "init")
    code, _out, err = run_cli(
        repo, "workflow", "gate", "sec_scan", "--command", "echo hi", "--required"
    )
    assert code == FAIL, "it created an inert requirement and reported success"
    assert "pipeline" in err, err
    assert run_cli(repo, "workflow")[0] == OK, "and left the workflow coherent"


def test_an_empty_pipeline_is_reported_by_the_reader_too(repo):
    """`workflow pipeline task ""` refuses — "a project with no checks at all" — while
    `check()` called the same state coherent. Two surfaces disagreeing about one
    config is how an operator learns to believe neither."""
    run_cli(repo, "init")
    cfg = _cfg(repo, task_pipeline=[], required=[])
    problems = [f for f in check(cfg, load_gates(repo, cfg)) if f.level == PROBLEM]
    assert any("no gates" in f.detail for f in problems), problems


def test_a_duplicate_gate_in_a_pipeline_is_reported(repo):
    """`describe` silently de-dupes, so the report did not even show it."""
    run_cli(repo, "init")
    cfg = _cfg(repo, task_pipeline=["implement", "unit_tests", "implement", "merge"])
    problems = [f for f in check(cfg, load_gates(repo, cfg)) if f.level == PROBLEM]
    assert any("twice" in f.detail or "duplicate" in f.detail for f in problems), problems


def test_a_gate_scoped_to_phases_is_not_silently_accepted_in_the_task_pipeline(repo):
    """`applies_to` was written by the new surface, advertised in the MCP tool
    description, and read by NOTHING — a dead knob the feature promised agents could
    use. Making the coherence check read it is what makes the promise true."""
    run_cli(repo, "init")
    cfg = Config.load(repo)
    gates = load_gates(repo, cfg)
    gates["unit_tests"].applies_to = "phase"
    problems = [f for f in check(cfg, gates) if f.level == PROBLEM]
    assert any("unit_tests" in f.subject and "phase" in f.detail for f in problems), problems


def test_concurrent_config_writes_do_not_lose_each_other(repo):
    """Read-modify-write with no lock, in a tool whose whole purpose is parallel agents.

    Two agents each running `ddflow workflow gate ...` is the normal case here, not an
    exotic one, and 40 concurrent pairs lost half their edits while every call returned
    success. The package already owns the file lock the event log uses.
    """
    import concurrent.futures as cf

    run_cli(repo, "init")
    keys = [f"gate.g{i}.command" for i in range(12)]

    def write(k: str) -> int:
        return run_cli(repo, "config", "--set", k, "echo x")[0]

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        assert all(code == OK for code in pool.map(write, keys))

    text = (repo / ".ddflow" / "config.toml").read_text()
    missing = [k for k in keys if k.rsplit(".", 2)[1] not in text]
    assert not missing, f"{len(missing)} of {len(keys)} edits reported success and vanished"
