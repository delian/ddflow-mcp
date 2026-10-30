"""An interrupted review that recorded no verdict consumes no review round (bug
Bfdcb5c177c).

A full review run killed mid-flight is recorded ``unavailable`` whose evidence carries
no reviewer verdict; the round budget must not charge it. One finished round plus one
interrupted run leave ONE round used. ``_delta_rounds`` asks each record's ``status``
("only ones that reached a reviewer"); the full-round counter must ask each of ITS
calls the same question, or a no-verdict record in the gate's history is charged a
round it never delivered.
"""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that finds FINDME; item T1 with a change that contains it."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf 'call\\n' >> {shlex.quote(str(seen))}\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    q = shlex.quote(str(cli)).replace("\\", "\\\\").replace('"', '\\"')
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{q}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    return seen


def _rounds(repo: Path, gate: str) -> int:
    return api._rounds_used(fold(EventLog(repo).read_all(), strict=False).items["T1"], gate)


def test_one_finished_round_plus_an_interrupted_review_leave_one_round_used(repo, tmp_path):
    """The repro of Bfdcb5c177c: one full review finishes and charges the round; the next
    full review is interrupted before a reviewer answers, and the gate records
    ``unavailable`` with no verdict. The budget shows ONE round used, not two."""
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == OK and out.data["outcome"] == "failed", out.reason
    assert _rounds(repo, "critic") == 1, "the finished review is the one round"
    log, cfg, st = api._load(repo, "")
    api._record(
        repo, log, cfg, st, "T1", "critic", "unavailable", None, None,
        reason="interrupted before a reviewer finished",
        evidence={"review_kind": "full", "status": "UNAVAILABLE"},
    )  # fmt: skip
    assert _rounds(repo, "critic") == 1, (
        "the interrupted review recorded no verdict: the budget must not charge it"
    )


def test_a_full_shaped_record_whose_status_says_no_verdict_charges_nothing(repo, tmp_path):
    """A record marked kind full but with a non-verdict status (the dead run's own words)
    is a round of nothing: the full counter must ask `status` exactly as the delta one
    does, or an interrupted run in the gate's history stays charged."""
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    log, cfg, st = api._load(repo, "")
    api._record(
        repo, log, cfg, st, "T1", "critic", "unavailable", None, None,
        reason="killed mid-flight", evidence={"review_kind": "full", "status": "UNAVAILABLE"},
    )  # fmt: skip
    assert _rounds(repo, "critic") == 1


def test_a_full_record_with_no_status_still_counts_not_a_silent_budget_reset(repo, tmp_path):
    """A full record whose status evidence predates `status` (a re-claim or manual record
    keeps `review_kind` without it) was a charged round; dropping it silently would hand
    the budget back, which is the reset `_full_rounds` promises not to do."""
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    log, cfg, st = api._load(repo, "")
    api._record(
        repo, log, cfg, st, "T1", "critic", "failed", None, None,
        reason="1 finding", evidence={"review_kind": "full"},
    )  # fmt: skip
    assert _rounds(repo, "critic") == 2


def test_the_signal_termination_record_of_a_dead_run_charges_nothing(repo, tmp_path):
    """`_terminated` records the SIGTERM'd review as ``unavailable`` (bed9d75dd1c):
    evidence with no verdict. Counting nothing for it is what keeps the kill free."""
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    log, cfg, st = api._load(repo, "")
    api._record(
        repo, log, cfg, st, "T1", "critic", "unavailable", None, None,
        reason="the review was terminated by SIGTERM before a reviewer finished",
        evidence={"review_kind": "delta"},
    )  # fmt: skip
    assert _rounds(repo, "critic") == 1


def test_a_status_less_record_of_a_refused_or_errored_run_charges_nothing(repo, tmp_path):
    """A status-less full record recorded ``unavailable`` -- how a refused or errored
    run is recorded -- delivered no verdict either: the no-`status` fallback must
    exclude it exactly as it excludes ``unavailable``, or the era-before-`status`
    refused/errored records are charged rounds they never delivered (rubber_duck
    finding #1)."""
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    log, cfg, st = api._load(repo, "")
    api._record(
        repo, log, cfg, st, "T1", "critic", "unavailable", None, None,
        reason="the unavailable run recorded no verdict",
        evidence={"review_kind": "full"},
    )  # fmt: skip
    assert _rounds(repo, "critic") == 1
