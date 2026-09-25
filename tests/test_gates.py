import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.services import gates as G

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


def item(log):
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})
    return fold(log.read_all())


# -- THE regression test for the bug this project's own bug-hunt found -----------------
@pytest.mark.parametrize(
    "command,expected",
    [
        ("definitely-not-a-real-binary-xyzzy --run", "unavailable"),
        ("true && definitely-not-a-real-binary-xyzzy", "unavailable"),
        ("exit 3", "failed"),
        ("exit 127", "failed"),  # a suite MAY exit 127 on purpose
        ("echo ok", "passed"),
        ("sh -c 'exit 1'", "failed"),
    ],
)
def test_a_missing_tool_is_unavailable_not_failed(gd, repo, command, expected):
    """A tool that vanished must never read as a check that ran and failed.

    Found by bug-hunting this module: under `shell=True` a missing binary exits 127,
    which the first implementation classified as `failed`. The consequence is the
    silent inverse of a vacuous pass — a linter nobody installed looks like a linter
    reporting problems, and worse, a *reviewer* that stopped being reachable looks
    like a reviewer with findings. Mutation-verified: reverting the pre-flight in
    `_missing_executable` turns the first two cases red.
    """
    gd["unit_tests"].command = command
    outcome, _ev = G.run_command_gate(gd["unit_tests"], repo)
    assert outcome == expected


def test_a_gate_requiring_evidence_cannot_pass_by_assertion(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="requires evidence"):
        G.record(log, cfg, "T1", "unit_tests", "passed", gates=gd)


def test_unavailable_must_carry_a_reason(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="must carry a --reason"):
        G.record(log, cfg, "T1", "critic", "unavailable", gates=gd)


def test_a_skip_must_be_explained(log, cfg, gd):
    item(log)
    with pytest.raises(ValueError, match="must carry --reason"):
        G.record(log, cfg, "T1", "dedupe", "skipped", gates=gd)
    G.record(log, cfg, "T1", "dedupe", "skipped", reason="no shared code touched", gates=gd)


def test_unavailable_is_not_a_pass_in_status(log, cfg, gd):
    item(log)
    G.record(log, cfg, "T1", "critic", "unavailable", reason="endpoint down", gates=gd)
    st = fold(log.read_all())
    s = G.status(st, cfg, "T1")
    assert "critic" in s.unavailable
    assert "critic" not in s.done
    assert not s.complete


def test_same_family_panel_is_not_independent(log, cfg, gd):
    item(log)
    G.record(
        log, cfg, "T1", "rubber_duck", "passed", evidence={"model": "claude-sonnet-5"}, gates=gd
    )
    G.record(
        log, cfg, "T1", "standards", "passed", evidence={"model": "claude-haiku-4-5"}, gates=gd
    )
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert not ok and "same as the author" in why
    G.record(log, cfg, "T1", "critic", "passed", evidence={"model": "gemini-2.5-pro"}, gates=gd)
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert ok and "google" in why


def test_no_reviewer_at_all_is_not_independence(log, cfg):
    item(log)
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert not ok and "no reviewer ran" in why


def test_gates_overlay_rather_than_replace(repo, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "pytest -q"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q"
    assert gates["bug_hunt"].prompt, "unmentioned gates must keep their defaults"


def test_an_unknown_gate_field_is_an_error_not_a_silent_drop(repo, cfg):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncomand = "typo"\n')
    with pytest.raises(ValueError, match="unknown field"):
        G.load_gates(repo, cfg)


def test_gate_config_is_read_from_config_toml_not_only_gates_toml(repo, cfg):
    """`ddflow configure` writes to config.toml; gates were read only from gates.toml.

    The result was a config write that reported success and changed nothing — the MCP
    `ddflow_configure` tool accepted `[gate.unit_tests]`, wrote it, said "appended",
    and the gate kept its default. Found by the state-aware-instructions test, which
    configured a project and was still told the project was unconfigured.
    Mutation-verified: dropping config.toml from the read list makes this red.
    """
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "pytest -q --tb=short"\n'
    )
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q --tb=short"


def test_gates_toml_wins_over_config_toml(repo, cfg):
    """Both are read; the more specific file decides, and neither is silently ignored."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "from-config"\ntimeout_s = 111\n'
    )
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "from-gates"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "from-gates"
    assert gates["unit_tests"].timeout_s == 111, "the config.toml overlay was discarded"


# -- B21: a probe is evidence about the tree it ran on ----------------------------------


def _repo_with_a_command_gate(repo, command="python3 -c 'print(1)'"):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = "{command}"\ncwd = "repo"\n'
    )


def test_the_runner_forbids_bytecode_caching(repo):
    """Same-second, same-size edits leave a valid-looking stale `.pyc`, so the run
    AFTER a patch re-executes the code from BEFORE it — and reports a pass about source
    that is no longer there. The interpreter's own cache invalidation is a timestamp
    and a size, and an agent editing in a loop defeats both by accident."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(
        id="probe",
        command="python3 -c \"import os; print(os.environ.get('PYTHONDONTWRITEBYTECODE'))\"",
        cwd="repo",
    )
    _outcome, ev = run_command_gate(g, repo)
    assert "1" in ev["tail"], f"PYTHONDONTWRITEBYTECODE not set for the gate: {ev['tail']!r}"


def test_a_gate_result_records_which_tree_it_ran_on(repo):
    """Evidence with no subject is an assertion. Two agents in two worktrees, or one
    agent editing between gates, and 'the tests passed' stops naming what it passed on."""
    from ddflow.services.gates import GateDef, run_command_gate

    _outcome, ev = run_command_gate(GateDef(id="probe", command="true", cwd="repo"), repo)
    assert ev.get("tree_sha"), f"no tree_sha in the evidence: {sorted(ev)}"


def test_the_tree_sha_changes_when_the_tree_does(repo):
    """Otherwise it is a constant wearing a fingerprint's name."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(id="probe", command="true", cwd="repo")
    before = run_command_gate(g, repo)[1]["tree_sha"]
    (repo / "new_file.py").write_text("x = 1\n")
    after = run_command_gate(g, repo)[1]["tree_sha"]
    assert before != after, "an untracked file appeared and the fingerprint did not move"


def test_an_unchanged_tree_keeps_the_same_sha(repo):
    """The other half: a fingerprint that changes every call identifies nothing."""
    from ddflow.services.gates import GateDef, run_command_gate

    g = GateDef(id="probe", command="true", cwd="repo")
    assert run_command_gate(g, repo)[1]["tree_sha"] == run_command_gate(g, repo)[1]["tree_sha"]


def test_completing_warns_when_a_passing_gate_ran_on_a_different_tree(repo):
    """The ordinary way it happens: run the tests, edit one more thing, complete.

    The recorded pass is then true about source nobody is shipping — and in the log it
    is indistinguishable from a pass about the code that shipped. A warning, not a
    block: the evidence is real, and refusing on a comment-sized change is how a check
    gets turned off.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "repo"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == OK
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")

    (repo / "a.py").write_text("changed after the tests ran\n")
    _code, _out, err = run_cli(repo, "complete", "T1")
    assert "different tree" in err, err


def test_it_stays_quiet_when_nothing_moved(repo):
    """A warning that always fires is one nobody reads."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "repo"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    run_cli(repo, "gate", "run", "T1", "unit_tests")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")
    _code, _out, err = run_cli(repo, "complete", "T1")
    assert "different tree" not in err, err
