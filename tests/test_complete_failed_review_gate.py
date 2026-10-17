"""D-failed-review-gate (bug B85841c0846): `merge` and `complete` refuse a review gate whose
latest outcome is failed unless every confirmed finding is followed by a later delta round
that did not raise it again; `--force` does not lift it, and the override is the operator's
`gate skip --reason`, which an agent identity cannot grant."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

BLOCKER = "failed with findings not re-reviewed"


def _setup(repo: Path, tmp_path: Path) -> None:
    """T1 on branch `feat`; the reviewer flags FINDME (x.py) and, first, FINDME-B (y.py)."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat | grep '^+')\n"
        'case "$in" in *FINDME-B*) printf \'FINDING MEDIUM y.py:1\\nsecond problem\\n\\n'
        "STATUS: FINDINGS 1\\n';; "
        "*FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\ndelta_default = true\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "big.py").write_text("".join(f"line_{n} = {n}\n" for n in range(400)))
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"change {name}")


def _failed_and_confirmed(repo: Path) -> None:
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "failed", out.data
    t = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="fixed in x.py")
    assert t.exit == 0, t.reason


def _complete(repo: Path, *extra: str) -> tuple[int, str]:
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", *extra)
    return code, out + err


def test_failed_gate_with_confirmed_findings_and_no_delta_round_is_refused(repo, tmp_path):
    """The DX-worktree-claim-ready case: failed, 1 confirmed finding, 0 delta rounds."""
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    code, text = _complete(repo)
    assert code == 3 and BLOCKER in text and "critic" in text, text
    code, text = _complete(repo, "--force")
    assert code == 3 and BLOCKER in text and "does not lift" in text, text
    code, out, err = run_cli(repo, "merge", "T1", "--branch", "feat")
    assert code == 3 and BLOCKER in out + err, out + err


def test_a_delta_round_that_did_not_raise_the_finding_again_clears_it(repo, tmp_path):
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    # fix #1, and the next change draws a NEW finding: the gate stays failed, #1 is answered
    _commit(repo, "x.py", "x = 1\n")
    _commit(repo, "y.py", "y = 1  # FINDME-B\n")
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "failed", out.data
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert len(ev["delta_raised"]) == 1 and ev["findings"] == 2, ev
    t = api.triage(repo, "T1", gate="critic", finding=2, verdict="confirmed", probe="y.py is wrong")
    assert t.exit == 0, t.reason
    _, text = _complete(repo)
    assert BLOCKER in text and "confirmed finding(s) " in text, text
    # the second finding gets its own delta round that does not raise it
    _commit(repo, "y.py", "y = 1\n")
    api.review(repo, gate="critic", item="T1", branch="feat")
    _, text = _complete(repo)
    assert BLOCKER not in text, text


def test_untriaged_findings_and_a_hand_recorded_failure_are_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    assert api.review(repo, gate="critic", item="T1", branch="feat").data["outcome"] == "failed"
    assert BLOCKER in _complete(repo)[1]
    run_cli(repo, "gate", "record", "T1", "rubber_duck", "--outcome", "failed", "--reason", "x")
    assert "rubber_duck" in _complete(repo)[1]


def test_the_operator_override_is_recorded_and_an_agent_cannot_grant_it(
    repo, tmp_path, monkeypatch
):
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "critic", "--reason", "operator accepts", agent="coder"
    )
    assert code == 3 and "operator" in out + err, out + err
    assert BLOCKER in _complete(repo)[1]
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    code, out, err = run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "operator accepts")
    assert code == 0, out + err
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    assert rec.outcome == "skipped" and "operator accepts" in (rec.reason or ""), rec
    assert BLOCKER not in _complete(repo)[1]


@pytest.mark.parametrize("verdict", ["refuted"])
def test_a_gate_whose_findings_were_all_refuted_is_not_blocked(repo, tmp_path, verdict):
    _setup(repo, tmp_path)
    assert api.review(repo, gate="critic", item="T1", branch="feat").data["outcome"] == "failed"
    t = api.triage(repo, "T1", gate="critic", finding=1, verdict=verdict, probe="ran it: fine")
    assert t.exit == 0, t.reason
    assert BLOCKER not in _complete(repo)[1]


def test_a_clearing_delta_before_a_full_round_that_raises_the_finding_again_clears_nothing(
    repo, tmp_path
):
    """The rubber_duck's finding on this fix: R1 raises #1, R2 (delta) does not, R3 (full)
    raises it again -- the latest failure is unanswered."""
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    _commit(repo, "z.py", "z = 1\n")  # a delta round that does not raise #1 (still failed: held)
    api.review(repo, gate="critic", item="T1", branch="feat")
    out = api.review(repo, gate="critic", item="T1", branch="feat", full=True)
    assert out.data["outcome"] == "failed", out.data
    assert BLOCKER in _complete(repo)[1]


def test_a_missing_gate_definition_does_not_excuse_a_failed_built_in_review_gate(repo, tmp_path):
    """rubber_duck's finding on the delta: no definitions supplied must not fail open."""
    from ddflow.config import Config
    from ddflow.services.gates import failed_review_blockers

    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    assert failed_review_blockers(st, Config.load(repo), "T1", None), "fail-open without defs"


def test_a_confirmed_finding_a_later_full_round_dropped_still_needs_a_delta(repo, tmp_path):
    """The critic's finding: the check looked only at the latest record's findings."""
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)  # R1 raises #1, confirmed
    _commit(repo, "x.py", "x = 1\n")
    _commit(repo, "y.py", "y = 1  # FINDME-B\n")
    out = api.review(repo, gate="critic", item="T1", branch="feat", full=True)  # R2: only #B
    assert out.data["outcome"] == "failed", out.data
    t = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="y.py is fine")
    assert t.exit == 0, t.reason
    assert BLOCKER in _complete(repo)[1]


def test_any_gate_with_a_reviewer_counts_not_only_different_family_ones(repo, tmp_path):
    """The critic's finding on the skip guard: same_family_ok review gates were left out."""
    from dataclasses import replace

    from ddflow.config import Config
    from ddflow.services.gates import failed_review_blockers
    from ddflow.services.gates.defs import DEFAULT_GATES

    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    own = {"critic": replace(DEFAULT_GATES["critic"], reviewer="same_family_ok")}
    assert failed_review_blockers(st, Config.load(repo), "T1", own)


def test_an_agent_cannot_skip_through_gate_record_either(repo, tmp_path):
    """roborev on this fix: `gate record --outcome skipped` was a second way past the guard."""
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--outcome",
        "skipped",
        "--reason",
        "timed out",
        agent="coder",
    )
    assert code == 3 and "operator" in out + err, out + err
    assert BLOCKER in _complete(repo)[1]


@pytest.mark.parametrize("outcome", ["unavailable", "partial", "passed"])
def test_an_agent_cannot_replace_the_failure_by_a_hand_record_either(repo, tmp_path, outcome):
    """roborev, three times over: any other outcome recorded over a failed review gate whose
    confirmed findings no delta re-checked would clear the blocker."""
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    extra = ["--evidence", "reviewed by hand"] if outcome == "passed" else []
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--outcome",
        outcome,
        "--reason",
        "x",
        *extra,
        agent="coder",
    )
    assert code == 3 and "operator" in out + err, out + err
    assert BLOCKER in _complete(repo)[1]
    assert "--force does not lift" in _complete(repo)[1]


def test_an_honest_unavailable_over_untriaged_findings_is_still_recorded(repo, tmp_path):
    """The reviewer being down is recorded as such (tests/test_gate_status_latest_round.py);
    only a CONFIRMED defect nobody re-checked needs the operator to be set aside."""
    _setup(repo, tmp_path)
    assert api.review(repo, gate="critic", item="T1", branch="feat").data["outcome"] == "failed"
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "critic", "--outcome", "unavailable", "--reason", "down",
        agent="coder",
    )  # fmt: skip
    assert code == 0, out + err


def test_recording_failed_again_over_a_failed_gate_is_not_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--outcome",
        "failed",
        "--reason",
        "x",
        agent="coder",
    )
    assert code in (0, 1), out + err
    assert "operator" not in out + err, out + err


def test_findings_that_cannot_be_read_back_are_not_no_findings(repo, tmp_path, monkeypatch):
    """roborev: a lost findings blob must not let a hand record clear the blocker."""
    from ddflow.core import blobref as B
    from ddflow.services.gates import failed_review as FR

    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    monkeypatch.setattr(
        FR.LB, "full", lambda ev: {"chunk_findings": B.Unavailable("abc", "is missing")}
    )
    assert "cannot be read back" in FR.unresolved_review_failure(it, "critic", confirmed_only=True)
