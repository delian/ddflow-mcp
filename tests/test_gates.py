import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard import gates as G
from orchard.model import fold


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
    (repo / ".orchard").mkdir(exist_ok=True)
    (repo / ".orchard" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "pytest -q"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q"
    assert gates["bug_hunt"].prompt, "unmentioned gates must keep their defaults"


def test_an_unknown_gate_field_is_an_error_not_a_silent_drop(repo, cfg):
    (repo / ".orchard").mkdir(exist_ok=True)
    (repo / ".orchard" / "gates.toml").write_text('[gate.unit_tests]\ncomand = "typo"\n')
    with pytest.raises(ValueError, match="unknown field"):
        G.load_gates(repo, cfg)


def test_gate_config_is_read_from_config_toml_not_only_gates_toml(repo, cfg):
    """`orchard configure` writes to config.toml; gates were read only from gates.toml.

    The result was a config write that reported success and changed nothing — the MCP
    `orchard_configure` tool accepted `[gate.unit_tests]`, wrote it, said "appended",
    and the gate kept its default. Found by the state-aware-instructions test, which
    configured a project and was still told the project was unconfigured.
    Mutation-verified: dropping config.toml from the read list makes this red.
    """
    (repo / ".orchard").mkdir(exist_ok=True)
    (repo / ".orchard" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "pytest -q --tb=short"\n'
    )
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "pytest -q --tb=short"


def test_gates_toml_wins_over_config_toml(repo, cfg):
    """Both are read; the more specific file decides, and neither is silently ignored."""
    (repo / ".orchard").mkdir(exist_ok=True)
    (repo / ".orchard" / "config.toml").write_text(
        '[gate.unit_tests]\ncommand = "from-config"\ntimeout_s = 111\n'
    )
    (repo / ".orchard" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "from-gates"\n')
    gates = G.load_gates(repo, cfg)
    assert gates["unit_tests"].command == "from-gates"
    assert gates["unit_tests"].timeout_s == 111, "the config.toml overlay was discarded"
