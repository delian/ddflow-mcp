"""A read/listing of a review's findings prints findings first and records no new round
(bug Bdd43b55a2a).

Reading the recorded round must never touch the review runner: the read attempts that
found this bug spent live review rounds against the budget. `ddflow review triage <id>`
with NO triage flag is the listing -- the same read its MCP twin performs -- so it prints
the recorded round's numbered findings FIRST, records nothing, and only a triage flag
turns it into a triage. The API's missing-verdict refusal leads with the findings too,
so the list is never hidden behind an error line.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_triage import _full_review, _setup, _state

import ddflow.api.review as api
from ddflow.api.review import _rounds_used
from ddflow.infra.log import EventLog

OK, FAIL = 0, 1


def _kinds(repo: Path) -> list[str]:
    return [e.kind for e in EventLog(repo).read_all()]


def test_a_listing_records_no_new_round_and_prints_findings_first(repo, tmp_path):
    _setup(repo, tmp_path)
    # Count every reviewer invocation: a listing must not add one (the read attempts that
    # gave this bug its name did -- live review rounds against the budget).
    marker = tmp_path / "reviewer-runs"
    fake = tmp_path / "fake-reviewer"
    parts = fake.read_text().splitlines(keepends=True)
    parts.insert(1, f"echo run >> '{marker}'\n")  # below the shebang
    fake.write_text("".join(parts))
    _full_review(repo)
    before = _rounds_used(_state(repo), "critic")
    runs_before = marker.exists() and marker.read_text().strip().splitlines() or []
    code, out, err = run_cli(repo, "review", "triage", "T1", "--gate", "critic")
    assert code == OK, (out, err)
    assert "#1 [" in out and "#2 [" in out, (out, err)
    # findings first: the numbered findings lead, the triage recipe follows
    assert out.index("#1 [") < out.index("record a verdict"), out
    # a pure read: the round stands as it was, no reviewer ran again, no triage recorded
    assert _rounds_used(_state(repo), "critic") == before
    assert marker.exists() and marker.read_text().strip().splitlines() == runs_before
    assert _kinds(repo).count("review.triaged") == 0


def test_a_triage_missing_the_verdict_still_prints_the_findings_first(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    before = _rounds_used(_state(repo), "critic")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="")
    assert out.exit == FAIL
    assert "--refuted (the probe" in out.reason
    # the numbered findings come BEFORE the remedy, so the read is served first
    assert out.reason.index("#1 [") < out.reason.index("say which"), out.reason
    # the refusal records nothing: no round, no triage event
    assert _rounds_used(_state(repo), "critic") == before
    assert _kinds(repo).count("review.triaged") == 0
