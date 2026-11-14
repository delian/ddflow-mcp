"""Delta rounds on the gate status, CLI, MCP, docs and the held-record release (split from test_review_delta.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from readme_corpus import readme_corpus
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


def test_gate_status_shows_full_and_delta_rounds_separately(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    _review(repo)
    _fix(repo, "g.py")
    _review(repo)
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert code == OK, err
    assert "1 full round, 2 delta rounds" in out, out


def test_the_cli_has_full_and_refuses_it_with_delta(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--full", "--delta")
    assert code == FAIL and "--full" in err, err
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat")
    assert code == OK, err
    code, _o, err = run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat", "--full")
    assert code == OK, err


def _mcp(repo, name, **args):
    from ddflow.surfaces.mcp import Server

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return json.dumps(reply)


def test_mcp_review_takes_full_and_runs_the_same_default(repo, tmp_path):
    _setup(repo, tmp_path)
    _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat")
    _fix(repo)
    reply = _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat")
    assert "delta review of 1 commit since" in reply, reply
    assert _ev(repo)["review_kind"] == "delta"
    _fix(repo, "e.py")
    _mcp(repo, "ddflow_review", id="T1", gate="critic", branch="feat", full=True)
    assert _ev(repo)["review_kind"] == "full"


def test_mcp_configure_sets_it_on_both_layers_and_tells_the_operator(repo, tmp_path):
    _setup(repo, tmp_path)
    shared = _mcp(repo, "ddflow_configure", set="review.delta_default", value="false")
    assert "operator" in shared.lower(), shared
    assert Config.load(repo, env={}).review.delta_default is False
    local = _mcp(repo, "ddflow_configure", set="review.delta_default", value="true", local=True)
    assert "operator" in local.lower(), local
    cfg = Config.load(repo, env={})
    assert cfg.review.delta_default is True and cfg.sources["review.delta_default"] == "local"


def test_it_holds_under_a_custom_workflow(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,critic,merge")
    assert code == OK, err
    _review(repo)
    _fix(repo)
    _review(repo)
    assert _ev(repo)["review_kind"] == "delta"


# -- shipped with the package ------------------------------------------------------------


def test_the_shipped_docs_describe_it_and_name_no_repository():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "README.md",
        "ddflow/templates/drivers/implement-phase.md",
        "ddflow/templates/prompts/help/gates.md",
        "ddflow/templates/prompts/help/manual.md",
    ):
        text = readme_corpus() if rel == "README.md" else (root / rel).read_text()
        assert "delta_default" in text and ("--full" in text or "full=true" in text), rel


# -- findings of the review of this change ----------------------------------------------


def test_force_is_a_full_round_even_when_the_default_would_make_it_a_delta(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 2\n")  # the shipped cap
    _review(repo)
    _review(repo, full=True)  # the cap (2) is spent
    _fix(repo)
    out = _review(repo, force=True, reason="operator asked")
    assert out.exit == OK, out.reason
    ev = _ev(repo)
    assert ev["review_kind"] == "full" and ev["round"] == 3 and ev["budget_forced"]
    assert "delta review" not in out.data["text"]


def test_a_delta_that_reached_no_reviewer_does_not_move_the_reviewed_head(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    head = _ev(repo)["reviewed_head"]
    _fix(repo, "a.py")
    (tmp_path / "fake-reviewer").write_text("#!/bin/sh\nexit 1\n")  # the reviewer is down
    down = _review(repo)
    assert down.exit != OK
    assert _ev(repo)["reviewed_head"] == head, "no reviewer saw this head: the old one stays"
    (tmp_path / "fake-reviewer").write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n"
    )
    _fix(repo, "b.py")
    out = _review(repo)
    assert out.exit == OK, out.reason
    assert "delta review of 2 commits since" in out.data["text"], out.data["text"]


def test_a_plain_review_after_a_gate_skip_is_still_a_delta(repo, tmp_path):
    """Probe for a review finding: the log still knows the reviewed head after a skip
    replaced the gate's record, and a delta from it is sound (the skip changed no code)."""
    _setup(repo, tmp_path)
    _review(repo)
    run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "x")
    _fix(repo)
    out = _review(repo)
    assert out.exit == OK and _ev(repo)["review_kind"] == "delta", out.reason


def test_the_evidence_carries_the_resolved_round_kind_not_the_flag(repo, tmp_path):
    """Probe for a review finding: `full` is an argument, never an evidence field."""
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    _review(repo, full=False)
    ev = _ev(repo)
    assert "full" not in ev and ev["review_kind"] == "delta"


def test_a_clean_delta_does_not_clear_findings_nobody_triaged(repo, tmp_path):
    """Probe for a review finding: the delta does not see an unfixed finding, so the
    record's own untriaged findings hold the gate; once triaged, the next clean delta passes."""
    _setup(repo, tmp_path)
    _review(repo)  # finding #1
    _fix(repo)
    held = _review(repo)
    assert held.data["outcome"] == "failed", held.data
    assert "no triage verdict" in held.data["text"]
    assert (
        api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="p").exit == OK
    )
    _fix(repo, "h.py")
    out = _review(repo)
    assert out.data["outcome"] == "passed", out.data


def test_an_unchanged_delta_after_settled_triage_passes_the_gate_and_counts_no_round(
    repo, tmp_path
):
    """B10ad449deb: a clean delta is held `failed` over untriaged earlier findings, and
    its NOTE says the next clean delta passes -- but no code changed since the reviewed
    head, so that delta has nothing to review. Now every finding has a verdict: the
    unchanged delta releases the held pass and counts no round, instead of refusing and
    keeping the gate failed until new commits arrive."""
    seen = _setup(repo, tmp_path, "[review]\nmax_rounds = 4\n")
    assert _review(repo).data["outcome"] == "failed"  # full round 1: FINDME finding
    _fix(repo)
    held = _review(repo)
    assert held.data["outcome"] == "failed" and _ev(repo)["delta_rounds"] == 1, held.data
    calls = _calls(seen)
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p").exit == OK
    out = _review(repo)  # unchanged since the reviewed head, every finding settled
    assert _calls(seen) == calls, "no reviewer ran for an unchanged tree"
    assert out.exit == OK, out.reason
    assert "passed ON REFUTATION" in out.data["text"], out.data["text"]
    assert _ev(repo)["held_pass"] is True, "the held clean delta is what gets released"
    assert _ev(repo)["delta_rounds"] == 1, "the unchanged delta spent no round"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "passed"


def test_an_unchanged_delta_with_findings_still_untriaged_stays_failed_and_counts_no_round(
    repo, tmp_path
):
    """B10ad449deb: the unchanged delta counts no round; without triage the gate stays
    failed and the answer says so, instead of a refusal that leaves no way to tell the
    no-op from a fix."""
    seen = _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    held = _review(repo)
    assert held.data["outcome"] == "failed", held.data
    assert _ev(repo)["delta_rounds"] == 1, "the held delta counted its round"
    calls = _calls(seen)
    out = _review(repo)  # unchanged since the reviewed head; the finding is untriaged
    assert out.exit == REFUSED, out.reason
    assert _calls(seen) == calls
    assert "stays failed" in out.reason and "review triage" in out.reason, out.reason
    assert "No round was counted" in out.reason
    assert _ev(repo)["delta_rounds"] == 1, "the unchanged delta spent no delta round"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "failed", "the gate stays failed"


def test_a_failed_delta_that_is_not_the_hold_is_never_released(repo, tmp_path):
    """B10ad449deb in reverse: `_held_and_settled` reads the `held_pass` marker, so a delta
    `failed` for its own finding (outcome failed without a clean review to hold) is not
    flipped to passed by an unchanged delta -- every finding settled, triage alone does not
    clear `fail` without the hold (the marker is what releases, and it is absent)."""
    _setup(repo, tmp_path)
    _review(repo)  # finding #1
    _fix(
        repo, "z.py", "z = 1  # FINDME-H\n"
    )  # finding #2 (HIGH), the delta's own: failed, NOT held
    held = _review(repo)
    assert held.data["outcome"] == "failed" and "held_pass" not in _ev(repo), held.data
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p").exit == OK
    assert api.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="p").exit == OK
    out = _review(repo)  # unchanged since the reviewed head; nothing held a clean pass
    assert out.exit == REFUSED and "stays failed" in out.reason, out.reason
    assert _ev(repo)["delta_rounds"] == 1
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "failed"


def test_a_held_record_from_before_the_marker_releases_by_shape(repo, tmp_path):
    """deepseek finding (rubber_duck delta): a record WRITTEN BY AN OLDER ddflow holds a
    clean delta with the marker never invented yet (`review_kind = delta`, no findings of
    its own, failed) -- it must release all the same once triaged, since only `_hold`
    can fail a reviewed delta with no finding."""
    _setup(repo, tmp_path)
    _review(repo)  # finding #1
    _fix(repo)
    held = _review(repo)
    assert held.data["outcome"] == "failed", held.data
    ev = dict(_ev(repo))
    assert ev["held_pass"] is True and ev["delta_findings"] == 0, ev
    assert _ev(repo)["delta_rounds"] == 1, "the held delta counted its round"
    log = EventLog(repo)
    log.append(
        "gate.failed",
        "T1",
        {
            "gate": "critic",
            "outcome": "failed",
            # the older version's record: the same evidence, no marker
            "evidence": {k: v for k, v in ev.items() if k != "held_pass"},
        },
    )
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="p").exit == OK
    out = _review(repo)  # unchanged: the shape releases without the marker
    assert out.exit == OK, out.reason
    assert "passed ON REFUTATION" in out.data["text"], out.data["text"]
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "passed"
    assert _ev(repo)["delta_rounds"] == 1


def test_force_with_delta_is_a_forced_delta_round(repo, tmp_path):
    """Ba93aec8448: `--force` is past the budget, not a full round: with `--delta` it is one."""
    _setup(repo, tmp_path)
    _review(repo)
    _fix(repo)
    out = _review(repo, force=True, reason="why", delta=True)
    assert out.exit == OK and _ev(repo)["review_kind"] == "delta", out.reason


def test_an_unreachable_delta_keeps_the_earlier_passed_round_and_notes_the_outage(repo, tmp_path):
    """Bug B277e2a224f: a delta that found the reviewer down replaced the gate's passed
    round with `unavailable`; the pass stays and the outage is an extra note on it."""
    from ddflow.core.model import fold

    _setup(repo, tmp_path)
    (tmp_path / "fake-reviewer").write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n"
    )
    assert _review(repo).exit == OK
    head = _ev(repo)["reviewed_head"]
    _fix(repo, "a.py")
    (tmp_path / "fake-reviewer").write_text("#!/bin/sh\nexit 1\n")  # the reviewer is down
    assert _review(repo).exit != OK
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    assert rec.outcome == "passed", rec
    ev = rec.evidence
    assert ev["reviewed_head"] == head and ev["unavailable_deltas"], ev


def test_an_empty_delta_over_only_excluded_files_keeps_the_earlier_passed_round(repo, tmp_path):
    """Bug Be82376964f: a delta over commits that change only excluded files (pins/golden/
    transcripts) found an empty diff and recorded UNAVAILABLE, replacing the earlier passed
    round and reading as a spent round. The empty delta keeps the earlier round (its outage
    noted under `unavailable_deltas`), calls no reviewer, and spends no delta round."""
    seen = _setup(repo, tmp_path)
    (tmp_path / "fake-reviewer").write_text(
        f"#!/bin/sh\ncat >/dev/null\nprintf 'call\\n' >> '{seen}'\necho 'STATUS: NO FINDINGS'\n"
    )
    assert _review(repo).exit == OK  # full round 1: clean pass
    head = _ev(repo)["reviewed_head"]
    calls = _calls(seen)
    _fix(repo, "snap.ambr", "regenerated\n")  # an excluded generated file, nothing else
    out = _review(repo, delta=True)
    assert _calls(seen) == calls, "no reviewer ran for a delta that left nothing to review"
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    assert rec.outcome == "passed", rec.outcome
    ev = rec.evidence
    assert ev["reviewed_head"] == head, "the earlier round's head stays"
    assert ev["unavailable_deltas"], "the empty delta is a note, not a replacement"
    assert ev.get("delta_rounds", 0) == 0, "the empty delta spent no delta round"
    assert "the earlier round stands" in out.data["text"], out.data["text"]
