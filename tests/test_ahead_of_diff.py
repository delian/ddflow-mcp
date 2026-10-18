"""fix-ahead-of-diff: the pipeline-order check judges a required gate's applies_when on the
actual diff, so a gate the diff excuses is not 'ahead' of later gates."""

from __future__ import annotations

from conftest import run_cli
from test_na_required_gate import _setup, _state, _worktree_with_change

from ddflow.services import completion as CM
from ddflow.services import gates as G
from ddflow.services.gates import measured as GR


def _ahead(repo, cfg, diff):
    st = _state(repo)
    return GR.gates_ahead_of(st, cfg, "T1", "merge", G.load_gates(repo, cfg), diff)


def test_an_excused_required_gate_is_not_ahead(repo):
    cfg = _setup(repo)
    _worktree_with_change(repo, "docs/x.md")
    st = _state(repo)
    ahead = _ahead(repo, cfg, lambda: CM.changed_paths(repo, st.items["T1"]))
    assert "unit_tests" not in ahead, ahead
    assert "unit_tests" in _ahead(repo, cfg, None)


def test_block_policy_does_not_refuse_on_an_excused_gate(repo):
    _setup(repo)
    run_cli(repo, "config", "--set", "gates.enforce_order", "block")
    _worktree_with_change(repo, "docs/x.md")
    for g in ("research", "rules", "implement", "rubber_duck", "critic", "standards"):
        code, out, err = run_cli(repo, "gate", "skip", "T1", g, "--reason", "t", agent="a1")
        assert code == 0, out + err
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "bug_hunt",
        "--outcome",
        "passed",
        "--evidence",
        "x",
        agent="a1",
    )
    assert code == 0 and "comes after" not in out + err, out + err


def test_block_policy_still_refuses_on_a_gate_the_diff_does_not_excuse(repo):
    _setup(repo)
    run_cli(repo, "config", "--set", "gates.enforce_order", "block")
    _worktree_with_change(repo, "docs/x.md")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "bug_hunt",
        "--outcome",
        "skipped",
        "--evidence",
        "x",
        agent="a1",
    )
    assert code != 0 and "comes after" in out + err and "rubber_duck" in out + err, out + err
