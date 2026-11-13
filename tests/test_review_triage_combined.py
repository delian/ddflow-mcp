"""A combined review stores each finding under every gate it stands for; triaging the
finding once -- under one gate -- answers the SAME finding (the same digest) under the
other gates too, so an agent is not asked to record the same verdict twice (bug
B-combined-review-findings-triaged-twice).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

GATES = "rubber_duck,critic"

OK = 0


def _setup(repo: Path, tmp_path: Path) -> None:
    """A command reviewer (gemini family) that flags a distinctive ``TRIAGE2COMBO`` line,
    serving both rubber_duck and critic; a small branch ``feat`` whose diff stays under
    [review].combined_under_lines, so ONE review is recorded for both gates."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        'case "$in" in *TRIAGE2COMBO*) printf \'FINDING HIGH x.py:1\\ncombo bad line\\n\\n'
        "STATUS: FINDINGS 1\\n';; *) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\nhedge = 1\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # TRIAGE2COMBO\ny = 2\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def _duck_and_critic(repo: Path):
    it = _state(repo)
    return G.triage_counts(it, "rubber_duck"), G.triage_counts(it, "critic")


def test_triaging_one_gate_of_a_combined_review_answers_the_other(repo, tmp_path):
    """The same digest stands under both gates; one --refuted settles both."""
    _setup(repo, tmp_path)
    api.review(repo, gate=GATES, item="T1", branch="feat")
    it = _state(repo)
    assert (
        it.gates["rubber_duck"].evidence["chunk_findings"]
        == it.gates["critic"].evidence["chunk_findings"]
    )

    out = api.triage(
        repo, "T1", gate="rubber_duck", finding=1, verdict="refuted", probe="x = 1 is intended"
    )
    assert out.exit == OK, out.reason

    duck, critic = _duck_and_critic(repo)
    assert duck == {"findings": 1, "refuted": 1, "confirmed": 0, "untriaged": 0}, duck
    assert critic == {"findings": 1, "refuted": 1, "confirmed": 0, "untriaged": 0}, critic


def test_a_confirmed_verdict_is_mirrored_to_each_gate_too(repo, tmp_path):
    """A --confirmed citation answers the same finding under every gate, not only the one
    named with --gate."""
    _setup(repo, tmp_path)
    api.review(repo, gate=GATES, item="T1", branch="feat")

    out = api.triage(
        repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="fixed in abc123"
    )
    assert out.exit == OK, out.reason

    duck, critic = _duck_and_critic(repo)
    assert critic == {"findings": 1, "refuted": 0, "confirmed": 1, "untriaged": 0}, critic
    assert duck == {"findings": 1, "refuted": 0, "confirmed": 1, "untriaged": 0}, duck


def test_mirroring_also_settles_the_sibling_gate_once_its_budget_is_spent(repo, tmp_path):
    """The mirror answers the sibling gate's copy of the finding, so the sibling gate is
    settled on refutation the same as the gate triaged outright (budget spent at 1 round)."""
    _setup(repo, tmp_path)
    (repo / ".ddflow" / "config.toml").write_text(
        (repo / ".ddflow" / "config.toml").read_text() + "[review]\nmax_rounds = 1\n"
    )
    api.review(repo, gate=GATES, item="T1", branch="feat")

    out = api.triage(
        repo, "T1", gate="rubber_duck", finding=1, verdict="refuted", probe="combo line is harmless"
    )
    assert out.exit == OK, out.reason

    it = _state(repo)
    assert it.gates["critic"].outcome == "passed", (
        "critic must settle once its mirrored finding is answered"
    )
    assert (it.gates["critic"].evidence or {}).get("passed_on_refutation")


def test_the_mirrored_sibling_settles_on_refutation_without_the_budget_spent(repo, tmp_path):
    """The CLI triage (review_settle) settles EVERY fully-refuted gate the mirror answered,
    not only the budget-spent path: the sibling gate is passed on refutation under the
    default config too."""
    import ddflow.api.review_settle as ST

    _setup(repo, tmp_path)
    api.review(repo, gate=GATES, item="T1", branch="feat")

    out = ST.triage(
        repo, "T1", gate="rubber_duck", finding=1, verdict="refuted", probe="combo line is harmless"
    )
    assert out.exit == OK, out.reason

    it = _state(repo)
    assert it.gates["rubber_duck"].outcome == "passed"
    assert it.gates["critic"].outcome == "passed", (
        "critic stays settled once its mirrored finding is answered"
    )
    assert (it.gates["critic"].evidence or {}).get("passed_on_refutation")


def test_the_budget_spent_triage_keeps_its_passed_on_refutation_flag(repo, tmp_path):
    """Once the budget is spent `_triage` records the gate passed on refutation and returns
    that flag; the settle loop must preserve it, not overwrite it False."""
    import ddflow.api.review_settle as ST

    _setup(repo, tmp_path)
    (repo / ".ddflow" / "config.toml").write_text(
        (repo / ".ddflow" / "config.toml").read_text() + "[review]\nmax_rounds = 1\n"
    )
    api.review(repo, gate=GATES, item="T1", branch="feat")

    out = ST.triage(
        repo, "T1", gate="rubber_duck", finding=1, verdict="refuted", probe="combo line is harmless"
    )
    assert out.exit == OK, out.reason
    assert out.data["passed_on_refutation"] is True


def _setup_shifted(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that flags a `FINDME` line at the line number in the ``wording``
    file, with the same claim; two single-gate reviews at different line numbers put the
    SAME finding under a different digest per gate (bug Bbd391bfef5)."""
    wording = tmp_path / "wording"
    wording.write_text("x.py:1|combo bad line")
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"w=$(cat '{wording}')\n"
        "loc=${w%%|*}\n"
        "det=${w#*|}\n"
        'case "$in" in '
        '*FINDME*) printf \'FINDING HIGH %s\\n%s\\n\\nSTATUS: FINDINGS 1\\n\' "$loc" "$det";; '
        '*) echo "STATUS: NO FINDINGS";; esac\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\nhedge = 1\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\ny = 2\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    return wording


def test_a_refuted_triage_carries_to_a_sibling_same_point_under_another_digest(repo, tmp_path):
    """The same claim at the same design point cited at a different line (a different
    digest) under the other gate is the SAME finding: one --refuted settles it there too,
    instead of asking for a separate triage per digest (bug Bbd391bfef5)."""
    wording = _setup_shifted(repo, tmp_path)
    api.review(repo, gate="rubber_duck", item="T1", branch="feat")
    wording.write_text("x.py:2|combo bad line")
    api.review(repo, gate="critic", item="T1", branch="feat")

    it = _state(repo)
    assert (
        it.gates["rubber_duck"].evidence["chunk_findings"][0]["digest"]
        != it.gates["critic"].evidence["chunk_findings"][0]["digest"]
    ), "the digests must differ for this test to exercise the design-point carry"

    out = api.triage(
        repo, "T1", gate="rubber_duck", finding=1, verdict="refuted", probe="combo line is harmless"
    )
    assert out.exit == OK, out.reason

    duck, critic = _duck_and_critic(repo)
    assert duck == {"findings": 1, "refuted": 1, "confirmed": 0, "untriaged": 0}, duck
    assert critic == {"findings": 1, "refuted": 1, "confirmed": 0, "untriaged": 0}, critic
