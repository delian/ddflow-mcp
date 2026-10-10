"""A branch behind a gate-config change on main is told so, not handed a test failure.

Which gate definition runs and which tree it runs against are chosen separately:
`load_gates` reads the PRIMARY checkout's `.ddflow/config.toml` (every tree's `repo_root`
is the primary), while `run_command_gate` executes in the item's worktree, with the
branch's own code and dependencies. The config-knob half of this class is
tests/test_config_forward_compat.py; this is the gate-command half.

The live failure (bug B8ea7a90aea): main switched `unit_tests` to `pytest -n 48` in the
same change that added pytest-xdist. Every branch cut before it ran main's command against
its own dependencies, exited 4 with `unrecognized arguments: -n 48`, and was recorded as
a test FAILURE -- sending its author to fix tests that were not broken.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING = 0, 1, 2


def _commit_gates(repo: Path, command: str, msg: str) -> None:
    (repo / ".ddflow" / "gates.toml").write_text(f'[gate.unit_tests]\ncommand = "{command}"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _item_tree(repo: Path, item: str) -> Path:
    code, out, err = run_cli(repo, "--json", "show", item)
    assert code == 0, err
    tree = json.loads(out).get("worktree") or ""
    assert tree, f"{item} was claimed without a worktree: {out}"
    return Path(tree)


def _setup(repo: Path, old: str) -> Path:
    """`repo` with `unit_tests = old` committed and T1 claimed into its own worktree."""
    run_cli(repo, "init")
    _commit_gates(repo, old, "gate config")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == 0, out + err
    return _item_tree(repo, "T1")


def _recorded(repo: Path) -> tuple[str, str]:
    g = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"]
    return g.outcome, g.reason


def test_a_branch_cut_before_a_gate_command_change_reports_the_drift(repo):
    """The regression: main's new command fails on the old branch for want of what main
    added alongside it (here a marker file standing in for pytest-xdist)."""
    tree = _setup(repo, "true")
    (repo / "xdist.marker").write_text("")
    _commit_gates(repo, "test -f xdist.marker", "parallel tests")

    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome != "failed", f"a stale branch was recorded as a test FAILURE: {out}{err}"
    assert outcome == "unavailable" and code == NOTHING
    assert "behind a gate-config change on main" in reason, reason
    assert "merge main" in reason, reason

    # Merging main is the remedy the message names, and it works.
    _git(tree, "merge", "-q", "main")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == OK, out + err
    assert tree.exists()


def test_a_real_failure_on_an_up_to_date_branch_is_still_a_failure(repo):
    _setup(repo, "false")
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == FAIL
    assert _recorded(repo)[0] == "failed"


def test_a_pass_under_drift_stands(repo):
    """Main's command passing on the old branch is exactly what the gate asks: no reason
    to withhold it."""
    _setup(repo, "false")
    _commit_gates(repo, "true", "fix the command")
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == OK
    assert _recorded(repo)[0] == "passed"


def test_a_branch_that_changes_the_gate_itself_keeps_its_failure(repo):
    """The drift is the branch's OWN change, not main's: merging main fixes nothing, so
    the failure stands -- named, so the author knows which command ran."""
    tree = _setup(repo, "false")
    _commit_gates(tree, "true", "branch rewrites the gate")
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert code == FAIL and outcome == "failed"
    assert "this branch changes" in reason, reason


def test_only_what_changes_the_run_counts_as_drift(repo):
    """A reworded prompt on main does not make a real failure unavailable."""
    _setup(repo, "false")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "false"\nprompt = "reworded"\n'
    )
    _git(repo, "commit", "-qam", "reword")
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == FAIL
    assert _recorded(repo)[0] == "failed"


def test_an_uncommitted_command_in_the_primary_is_not_drift(repo):
    """The operator just set the command and has not committed it: merging main would
    bring nothing, and the command that ran IS the configured one -- its failure stands."""
    _setup(repo, "true")
    (repo / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "false"\n')
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == FAIL
    assert _recorded(repo)[0] == "failed"


def test_an_uncommitted_tweak_in_the_primary_does_not_hide_a_committed_change(repo):
    """The drift is decided on COMMITTED definitions: the primary's working tree adding
    a flag on top of main's committed change left the branch just as far behind."""
    _setup(repo, "true")
    (repo / "xdist.marker").write_text("")
    _commit_gates(repo, "test -f xdist.marker", "parallel tests")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "test -f xdist.marker -a -f xdist.marker"\n'
    )
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome == "unavailable", f"{outcome}: {reason} {out}{err}"
    assert "merge main" in reason and code == NOTHING


def test_an_uncommitted_edit_in_the_tree_does_not_make_a_behind_branch_its_own(repo):
    """An agent mid-edit of the branch's gate block has not changed the definition the
    branch carries: it is still behind main, and told to merge it."""
    tree = _setup(repo, "true")
    (repo / "xdist.marker").write_text("")
    _commit_gates(repo, "test -f xdist.marker", "parallel tests")
    (tree / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "true -x"\n')
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome == "unavailable", f"{outcome}: {reason} {out}{err}"
    assert "merge main" in reason and code == NOTHING


def test_a_local_override_of_the_command_is_what_ran_so_its_failure_stands(repo):
    """`.ddflow/local/` wins over both committed files in `load_gates`: when it sets the
    command, main's committed change never ran, and the failure is this branch's own."""
    _setup(repo, "true")
    (repo / "xdist.marker").write_text("")
    _commit_gates(repo, "test -f xdist.marker", "parallel tests")
    local = repo / ".ddflow" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "gates.toml").write_text('[gate.unit_tests]\ncommand = "false"\n')
    code, _out, _err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome == "failed", f"{outcome}: {reason}"
    assert code == FAIL


def test_a_branch_with_its_own_gate_edit_is_still_behind_a_base_change(repo):
    """Main's committed change is what ran, so the branch is behind whatever it edited
    itself: its own edit takes effect only after it merges, and so does main's."""
    tree = _setup(repo, "true")
    (tree / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ntimeout_s = 600\n'
    )
    _git(tree, "commit", "-qam", "branch edits the timeout")
    (repo / "xdist.marker").write_text("")
    _commit_gates(repo, "test -f xdist.marker", "parallel tests")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome == "unavailable", f"{outcome}: {reason} {out}{err}"
    assert "merge main" in reason and code == NOTHING


def test_drift_is_found_in_config_toml_too(repo):
    """The live case: main's `[gate.unit_tests]` lives in `.ddflow/config.toml`."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gate.unit_tests]\ncommand = "true"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gate config")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1", agent="worker")[0] == 0
    (repo / "xdist.marker").write_text("")
    cfg.write_text(cfg.read_text().replace('command = "true"', 'command = "test -f xdist.marker"'))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "parallel tests")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    outcome, reason = _recorded(repo)
    assert outcome == "unavailable", f"{outcome}: {reason} {out}{err}"
    assert "behind a gate-config change on main" in reason and code == NOTHING
