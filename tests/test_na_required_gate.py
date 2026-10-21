"""B-na-required-gate: a gate in `[gates].required` is never made not-applicable by the item's
DECLARED globs alone. Globs are agent-editable (`ddflow update --globs`, no approval), so
only the ACTUAL diff may excuse a required gate, and the excuse is recorded as evidence."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import completion as CM
from ddflow.services import gates as G


def _setup(repo, *, globs="docs/x.md"):
    """unit_tests (REQUIRED by default) applies only to src/**; the item declares `globs`."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", globs)
    assert run_cli(repo, "config", "--set", "gate.unit_tests.applies_when", '["src/**"]')[0] == 0
    cfg = Config.load(repo)
    assert "unit_tests" in G.required_gates(cfg)
    return cfg


def _state(repo):
    return fold(EventLog(repo).read_all())


def _worktree_with_change(repo, path: str) -> None:
    """Claim T1 in a real worktree and commit a change to ``path`` there."""
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    wt = _state(repo).items["T1"].worktree
    from ddflow.infra import worktree as W

    tree = W.load_path(repo, wt)
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tree), "add", path], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "change"], check=True)


def test_declared_globs_alone_never_excuse_a_required_gate(repo):
    cfg = _setup(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", G.load_gates(repo, cfg))
    assert "unit_tests" not in s.not_applicable, s.not_applicable
    assert not s.complete
    v = CM.verdict(st, cfg, "T1", repo=repo)
    assert any("unit_tests" in b for b in v.blockers), v.blockers


def test_gate_status_cli_keeps_the_required_gate_owed(repo):
    _setup(repo)
    out = run_cli(repo, "gate", "status", "T1")[1]
    assert "not applicable" not in out.split("unit_tests")[1].split("\n")[0], out


def test_a_diff_that_touches_the_gate_paths_keeps_it_owed_whatever_the_globs_say(repo):
    """The globs under-declare: the item says docs/x.md and changes src/a.py."""
    cfg = _setup(repo)
    _worktree_with_change(repo, "src/a.py")
    st = _state(repo)
    s = G.status(
        st, cfg, "T1", G.load_gates(repo, cfg), diff=lambda: CM.changed_paths(repo, st.items["T1"])
    )
    assert "unit_tests" not in s.not_applicable
    v = CM.verdict(st, cfg, "T1", repo=repo)
    assert any("unit_tests" in b for b in v.blockers), v.blockers


def test_a_diff_that_misses_the_patterns_excuses_the_required_gate_with_evidence(repo):
    cfg = _setup(repo)
    _worktree_with_change(repo, "lib/x.py")
    st = _state(repo)
    s = G.status(
        st, cfg, "T1", G.load_gates(repo, cfg), diff=lambda: CM.changed_paths(repo, st.items["T1"])
    )
    why = s.not_applicable.get("unit_tests", "")
    assert "src/**" in why and "lib/x.py" in why, s.not_applicable
    v = CM.verdict(st, cfg, "T1", repo=repo)
    assert not any("unit_tests" in b for b in v.blockers), v.blockers
    assert any("unit_tests" in n and "lib/x.py" in n for n in v.na_evidence), v.na_evidence


def test_an_unreadable_diff_never_excuses_a_required_gate(repo):
    cfg = _setup(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", G.load_gates(repo, cfg), diff=lambda: None)
    assert "unit_tests" not in s.not_applicable


def test_a_non_required_gate_declared_out_of_scope_is_rejudged_on_the_diff(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/x.md")
    run_cli(repo, "config", "--set", "gate.dedupe.applies_when", '["src/**"]')
    cfg = Config.load(repo)
    assert "dedupe" not in G.required_gates(cfg)
    st = _state(repo)
    defs = G.load_gates(repo, cfg)
    assert "dedupe" in G.status(st, cfg, "T1", defs).not_applicable  # declared globs, no diff yet
    s = G.status(st, cfg, "T1", defs, diff=lambda: ["src/a.py"])
    assert "dedupe" not in s.not_applicable


def test_brief_keeps_the_required_gate_in_gates_remaining(repo):
    """A declared-globs miss does not drop a required gate from 'gates remaining'."""
    _setup(repo)
    out = run_cli(repo, "brief", "--item", "T1")[1]
    remaining = next(ln for ln in out.splitlines() if "gates remaining" in ln)
    assert "unit_tests" in remaining, remaining


def test_brief_drops_the_required_gate_when_the_actual_diff_misses_it(repo):
    """The brief's `diff` wiring, excusing direction: a docs-only change excuses the gate."""
    _setup(repo)
    _worktree_with_change(repo, "lib/x.py")
    out = run_cli(repo, "brief", "--item", "T1", agent="a1")[1]
    remaining = next(ln for ln in out.splitlines() if "gates remaining" in ln)
    assert "unit_tests" not in remaining, remaining
