"""The completion rule-set, asked directly.

It used to be the body of `cli.cmd_complete` — real domain policy reachable only
through `main(argv)`. So "would this complete, and why not" could only be answered by
running the CLI and reading its stderr, and a rule could only be tested by driving a
subprocess. These tests call it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import completion as CM

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _state(repo: Path):
    return fold(EventLog(repo, "agent-test").read_all(), strict=False)


def _ask(repo: Path, item: str, **kw) -> CM.Verdict:
    return CM.verdict(_state(repo), Config.load(repo), item, repo=repo, **kw)


def test_the_rules_are_callable_without_argv(repo):
    """The whole point of B36. No subprocess, no stderr scraping, no parser."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    v = _ask(repo, "T1")
    assert isinstance(v, CM.Verdict)
    assert not v.may_complete, "a task with nothing recorded cannot complete"
    assert v.blockers, v


def test_every_unmet_condition_is_reported_together(repo):
    """Reporting the first one turns a single refusal into a round-trip per problem,
    and an agent that cannot see how many more are coming reaches for --force."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    v = _ask(repo, "T1")
    assert len(v.blockers) >= 2, f"only {len(v.blockers)} of several conditions: {v.blockers}"


def test_an_unknown_item_is_a_blocker_not_an_exception(repo):
    """A policy function that raises cannot be used to ASK — every caller would have to
    guard it, and the one that forgets crashes instead of refusing."""
    run_cli(repo, "init")
    v = _ask(repo, "NOPE")
    assert not v.may_complete
    assert "no such item" in v.blockers[0]


def test_open_sub_tasks_block_their_umbrella(repo):
    """Any item with work beneath it, not only a phase: completing an umbrella while
    its children are open marks work finished that nobody has done."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P1 fixture", "--globs", "p/**")
    run_cli(
        repo,
        "task",
        "add",
        "P1.T1",
        "--title",
        "P1.T1 fixture",
        "--phase",
        "P1",
        "--globs",
        "p/a.py",
    )
    v = _ask(repo, "P1")
    assert any("unfinished" in b for b in v.blockers), v.blockers


def test_silence_is_a_blocker_under_require_outcome(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    v = _ask(repo, "T1")
    assert any("never run and never skipped" in b for b in v.blockers), v.blockers


def test_a_skipped_non_required_gate_is_not_a_coverage_gap(repo):
    """Bb67dc543b6: only an UNSETTLED required gate counts as a coverage gap. A non-required
    pipeline gate recorded `skipped` is a settled outcome, never a gap and never a "never ran"
    note."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "dedupe",
        "--outcome",
        "skipped",
        "--reason",
        "nothing to dedupe",
    )
    v = _ask(repo, "T1")
    assert "dedupe" not in v.coverage_gaps, v.coverage_gaps


def test_a_required_skip_blocks_under_unavailable_is_failure(repo):
    """Bb67dc543b6: with [gates].unavailable_is_failure on, a REQUIRED gate recorded `skipped`
    blocks like a failure (the knob must not leave a skip as the one gap that slips through)."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "config", "gates.unavailable_is_failure", "true")
    run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "skipped",
        "--reason",
        "merge removed the worktree",
    )
    v = _ask(repo, "T1")
    assert any("coverage gap blocks" in b and "unit_tests" in b for b in v.blockers), v.blockers


def test_the_coverage_note_names_a_required_skip_a_skip(repo):
    """Bb67dc543b6: the coverage note calls a `skipped` required gate a skip, never a run."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "skipped",
        "--reason",
        "merge removed the worktree",
    )
    v = _ask(repo, "T1")
    assert "unit_tests skipped" in v.coverage_note, v.coverage_note


def test_it_writes_nothing(repo):
    """`verdict` is a question. A question with a side effect cannot be asked twice."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    before = sorted(p.read_bytes() for p in (repo / ".ddflow" / "events").glob("*.jsonl"))
    _ask(repo, "T1")
    _ask(repo, "T1")
    after = sorted(p.read_bytes() for p in (repo / ".ddflow" / "events").glob("*.jsonl"))
    assert before == after, "asking whether an item may complete appended to the log"


def test_the_surface_and_the_policy_agree(repo):
    """The refusal an operator sees must be the verdict the policy returned — that is
    what makes extracting it a refactor rather than a second implementation."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    v = _ask(repo, "T1")
    code, _out, err = run_cli(repo, "complete", "T1")
    assert code == REFUSED
    assert f"{len(v.blockers)} unmet condition(s)" in err, err
    for b in v.blockers:
        assert b.split("(")[0].strip()[:40] in err, f"the surface dropped: {b}"
