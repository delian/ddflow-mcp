"""Delta re-reviews (decision D-review-budget, item 2): [review].delta_default.

With the knob on, once a gate has a recorded review `ddflow review` reviews only the
commits since the head that review covered, merges the result into the gate's record,
and `--full` forces a full round. The shipped default is OFF since decision
D-gate-economy 3 (a re-review sends the whole diff with the previous findings; see
tests/test_gate_econ_rereview.py), so the projects here turn it on to test the delta
itself. Everything here runs in a throwaway project built by `ddflow init`: no
companions, no network, a command reviewer that is a shell script.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git
from review_delta_support import (
    FAIL,
    OK,
    REFUSED,
    _calls,
    _ev,
    _fix,
    _review,
    _setup,
)

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def test_the_default_is_off_for_every_project_with_no_config_line(repo):
    """D-gate-economy 3: a re-review is a full round over the whole diff by default."""
    run_cli(repo, "init")
    assert "delta_default" not in (repo / ".ddflow" / "config.toml").read_text()
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is False
    assert cfg.sources["review.delta_default"] == "default"
    assert cfg.unknown_knobs == []
    out = run_cli(repo, "config", "--explain", "--filter", "review.delta_default")[1]
    assert "review.delta_default" in out and "--full" in out and "[default]" in out


def test_every_layer_changes_it(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "config", "review.delta_default", "false")
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is False and cfg.sources["review.delta_default"] == "file"
    run_cli(repo, "config", "review.delta_default", "true", "--local")
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is True and cfg.sources["review.delta_default"] == "local"
    cfg = Config.load(repo, env={"DDFLOW_REVIEW_DELTA_DEFAULT": "0"})
    assert cfg.review.delta_default is False and cfg.sources["review.delta_default"] == "env"


def test_an_older_ddflow_reading_the_knob_notes_it_does_not_break():
    # the existing [review] unknown-key handling covers a tool built before this knob
    from ddflow.config import KNOB_DOCS

    doc = KNOB_DOCS["review.delta_default"]
    for private in ("ddflow-worktrees", "/home/", "bridge-cse", "w1-"):
        assert private not in doc


# -- the behaviour -----------------------------------------------------------------------


def test_after_a_first_review_a_one_commit_fix_is_reviewed_as_a_delta(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    first = _review(repo)
    assert first.exit == OK, first.reason
    whole = _ev(repo)["diff_chars"]
    assert _ev(repo)["review_kind"] == "full" and _ev(repo)["rounds"] == 1
    _fix(repo)
    before = _calls(seen)
    out = _review(repo)  # no flag: the default
    assert out.exit == OK, out.reason
    assert _calls(seen) > before
    ev = _ev(repo)
    assert ev["review_kind"] == "delta", ev
    assert ev["diff_chars"] < whole / 10, "the delta is the fix, not the whole diff"
    assert ev["rounds"] == 1, "a delta is not a full round"
    assert "delta review of 1 commit since" in out.data["text"], out.data["text"]
    assert ev["delta_rounds"] == 1


def test_two_fix_commits_are_counted(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo, "a.py")
    _fix(repo, "b.py")
    out = _review(repo)
    assert "delta review of 2 commits since" in out.data["text"], out.data["text"]


def test_full_forces_a_full_round_that_counts(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 2\n")  # the shipped cap
    _review(repo)
    whole = _ev(repo)["diff_chars"]
    _fix(repo)
    out = _review(repo, full=True)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["rounds"] == 2 and ev["round"] == 2
    assert ev["diff_chars"] >= whole
    assert "delta review" not in out.data["text"]
    # and it is bounded by [review].max_rounds like any full round
    _fix(repo, "c.py")
    refused = _review(repo, full=True)
    assert refused.exit == REFUSED and "max_rounds" in refused.reason


def test_full_and_delta_together_are_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    out = _review(repo, full=True, delta=True)
    assert out.exit == FAIL and "--full" in out.reason and "--delta" in out.reason


def test_a_rebased_branch_falls_back_to_a_full_round_and_says_why(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "work, rewritten")  # head is no ancestor now
    out = _review(repo)
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["rounds"] == 2
    text = out.data["text"]
    assert "full round" in text and "not an ancestor" in text and "rebased" in text, text


def test_the_explicit_delta_flag_also_falls_back_after_a_rebase(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")
    out = _review(repo, delta=True)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full"
    assert "not an ancestor" in out.data["text"]


def test_nothing_changed_since_the_reviewed_head_counts_no_round_and_names_full(repo, tmp_path):
    seen = _setup(repo, tmp_path)
    _review(repo)
    calls = _calls(seen)
    out = _review(repo)
    assert out.exit == REFUSED and "nothing changed" in out.reason and "--full" in out.reason, (
        out.reason
    )
    assert "No round was counted" in out.reason and _calls(seen) == calls
    assert _ev(repo)["delta_rounds"] == 0, "the unchanged delta spent no delta round"
    assert _ev(repo)["rounds"] == 1, "the unchanged delta spent no full round"


def test_delta_default_false_is_the_old_behaviour(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\ndelta_default = false\nmax_rounds = 0\n")
    _review(repo)
    whole = _ev(repo)["diff_chars"]
    _fix(repo)
    out = _review(repo)
    ev = _ev(repo)
    assert out.exit == OK and ev["review_kind"] == "full" and ev["rounds"] == 2
    assert ev["diff_chars"] >= whole
    assert "delta review" not in out.data["text"]
    # an explicit --delta still works with the default off
    _fix(repo, "d.py")
    assert _review(repo, delta=True).exit == OK and _ev(repo)["review_kind"] == "delta"


def test_a_first_review_and_one_after_an_unavailable_one_are_full(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "x")
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full"


def test_a_partial_prior_review_is_not_deltaed_over(repo, tmp_path):
    """A delta from a head whose review skipped chunks would never cover them."""
    _setup(repo, tmp_path, "[review]\nmax_rounds = 0\n")
    _review(repo)
    log = EventLog(repo)
    ev = dict(_ev(repo), status="PARTIAL")
    log.append("gate.partial", "T1", {"gate": "critic", "outcome": "partial", "evidence": ev})
    _fix(repo)
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "full", out.data["text"]
    assert "partial" in out.data["text"]


def test_a_commit_or_base_review_is_not_second_guessed(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    sha = _git(repo, "rev-parse", "HEAD")
    out = api.review(repo, gate="critic", item="T1", commit=sha)
    assert out.exit == OK and _ev(repo)["review_kind"] == "delta"
    assert "delta review of" not in out.data["text"]


def test_the_other_gate_is_independent(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    out = _review(repo, gate="rubber_duck")  # no rubber_duck review yet: a full round
    assert out.exit == OK and _ev(repo, "rubber_duck")["review_kind"] == "full"


# -- the merge into the gate record -----------------------------------------------------


def test_the_delta_merges_into_the_record_keeping_earlier_triage(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)  # finding #1: x.py FINDME
    assert (
        api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="fixed").exit
        == OK
    )
    _fix(repo, "y.py", "y = 1  # FINDME-B\n")  # a different finding
    out = _review(repo)
    assert out.exit == OK
    ev = _ev(repo)
    titles = [f["title"] for f in ev["chunk_findings"]]
    assert len(titles) == 2, ev["chunk_findings"]
    assert ev["delta_findings"] == 1
    st = fold(EventLog(repo).read_all(), strict=False)
    from ddflow.services import gates as G

    counts = G.triage_counts(st.items["T1"], "critic")
    # the earlier finding keeps its verdict (same digest); the new one is untriaged
    assert counts == {"findings": 2, "refuted": 0, "confirmed": 1, "untriaged": 1}, counts
    assert "#2" in out.data["text"], "the delta's finding says its place in the merged record"
