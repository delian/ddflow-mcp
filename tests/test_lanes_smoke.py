"""SW-live-smoke: a live lane run is judged from its stream and the event log, never its own word.

The stream lines are the shape kilo 7.8.18 really wrote on 2026-11-05 (`run --format json`, a
DeepSeek endpoint), cut to the fields read here. docs/ddflow/research/swarm-smoke.md has the runs.
"""

from __future__ import annotations

import json

from ddflow.core.events import Event
from ddflow.services.lanes import smoke as S
from ddflow.services.swarm import lanehooks_check as CK

LANE, ITEM = "lane-1", "toy"


def tool(name, status="completed", out="", error="", **inp):
    state = {"status": status, "input": inp}
    state.update({"error": error} if error else {"output": out})
    return json.dumps({"type": "tool_use", "part": {"tool": name, "state": state}})


def step_finish(total=100, reason="stop"):
    return json.dumps(
        {"type": "step_finish", "part": {"reason": reason, "tokens": {"total": total}}}
    )


GOOD = (
    json.dumps({"type": "step_start", "sessionID": "ses_1"}),
    tool("ddflow_ddflow_claim", out='{"schema":"claim@1"}', id=ITEM),
    tool("write", out="Wrote file successfully.", filePath="/wt/hello.txt", content="x"),
    tool("bash", out="[b 296e5b5] add hello", command="git commit -m x"),
    tool("ddflow_ddflow_heartbeat", out='{"renewed":true}', id=ITEM),
    tool("ddflow_ddflow_gate", out='{"schema":"gate_record@1"}', action="record", id=ITEM),
    step_finish(),
    json.dumps({"type": "text", "part": {"text": "Done."}}),
)


def ev(kind, agent=LANE, subject=ITEM, **data):
    return Event(kind, subject, data, agent)


def log():
    return [ev("lease.acquired", holder=LANE), ev("gate.passed", gate="implement")]


def judged(lines=GOOD, events=None, commits=1, exit_code=0):
    return S.judge(
        S.read_stream(lines),
        log() if events is None else events,
        item=ITEM,
        lane=LANE,
        commits=commits,
        exit_code=exit_code,
    )


def outcomes(v):
    return {c.step: c.status for c in v.checks}


# --- the stream ------------------------------------------------------------------------------


def test_read_stream_takes_the_real_shape():
    s = S.read_stream(GOOD)
    assert [c.name for c in s.calls] == ["claim", "write", "bash", "heartbeat", "gate"]
    assert s.calls[4].action == "record"
    assert all(c.status == S.OK for c in s.calls)
    assert s.tokens == 100
    assert s.unreadable == 0


def test_a_line_that_is_not_json_is_counted_not_fatal():
    s = S.read_stream(["not json", "", *GOOD])
    assert s.unreadable == 1 and len(s.calls) == 5


def test_a_token_count_that_is_not_a_number_is_ignored():
    odd = [
        '{"type":"step_finish","part":{"tokens":{"total":"many"}}}',
        '{"type":"step_finish","part":{"tokens":{"total":true}}}',
        step_finish(7),
    ]
    assert S.read_stream(odd).tokens == 7


def test_a_ddflow_refusal_inside_a_completed_call_is_a_refusal():
    """Live: a refused gate record arrives as status `completed` with the refusal in the output."""
    out = '{"refusal":{"reason":"no commit ahead","outcome":"refused","exit":3},"schema":"x"}\n\nno commit'
    s = S.read_stream([tool("ddflow_ddflow_gate", out=out, action="record")])
    assert s.calls[0].status == S.REFUSED
    assert "no commit ahead" in s.calls[0].detail


def test_a_refusal_with_no_reason_is_still_a_refusal():
    s = S.read_stream([tool("ddflow_ddflow_heartbeat", out='{"refusal":{"reason":""}}')])
    assert s.calls[0].status == S.REFUSED


def test_a_lease_hook_error_is_a_guard_refusal_not_a_bad_call():
    no_lease = f"lane {LANE} holds no live lease: stop; do not write any file"
    glob = f"lane {LANE}: other.txt is outside the item's lease globs (hello.txt); do not edit it"
    s = S.read_stream([tool("write", "error", error=no_lease), tool("write", "error", error=glob)])
    assert [c.status for c in s.calls] == [S.GUARDED, S.GUARDED]


def test_the_guard_markers_are_the_sentences_the_hook_really_prints(tmp_path, monkeypatch):
    monkeypatch.setattr(CK, "_mine", lambda *_: ([], []))
    assert S.is_guard_refusal(CK.verdict(tmp_path, LANE, ["a.txt"], tmp_path)[1])
    monkeypatch.setattr(CK, "_mine", lambda *_: (["hello.txt"], []))
    code, why = CK.verdict(tmp_path, LANE, ["a.txt"], tmp_path)
    assert code == CK.REFUSE and S.is_guard_refusal(why)


def test_a_rejected_argument_is_a_bad_call():
    bad = "ddflow_memory action=similar: unknown argument(s): sources. Known: text"
    s = S.read_stream([tool("ddflow_ddflow_memory", "error", error=bad, action="similar")])
    assert s.calls[0].status == S.BAD


# --- reliability -----------------------------------------------------------------------------


def test_reliability_counts_harness_tools_and_guard_refusals_as_well_formed():
    lines = [*GOOD, tool("write", "error", error=f"lane {LANE} holds no live lease: stop")]
    r = S.reliability(S.read_stream(lines).calls)
    assert (r.calls, r.bad, r.rate) == (6, 0, 1.0)


def test_reliability_of_nothing_has_no_rate():
    r = S.reliability(())
    assert r.calls == 0 and r.rate is None


def test_a_bad_call_lowers_the_rate():
    bad = tool("ddflow_ddflow_memory", "error", error="unknown argument(s): sources")
    r = S.reliability(S.read_stream([*GOOD, bad]).calls)
    assert r.bad == 1 and r.rate == 5 / 6


# --- the judgement ---------------------------------------------------------------------------


def test_the_verdict_carries_the_runs_tool_call_reliability():
    assert judged().reliability == S.Reliability(calls=5, bad=0)


def test_a_clean_run_passes_every_step():
    v = judged()
    assert v.outcome == S.PASSED
    assert set(outcomes(v)) == set(S.STEPS)
    assert set(outcomes(v).values()) == {S.PASSED}


def test_the_lanes_word_is_not_evidence():
    """Every tool call in the stream, nothing in the log: the claim did not happen."""
    v = judged(events=[])
    assert outcomes(v)["claim"] == S.FAILED
    assert outcomes(v)["gate_record"] == S.FAILED
    assert v.outcome == S.FAILED


def test_an_event_under_another_identity_does_not_count():
    v = judged(events=[ev("lease.acquired", agent="someone-else", holder="someone-else")])
    assert outcomes(v)["claim"] == S.FAILED


def test_the_claims_own_renewal_is_not_a_heartbeat():
    """Live: claiming writes a `lease.renewed`, and a real heartbeat soon after it writes none."""
    lines = [line for line in GOOD if "heartbeat" not in line]
    v = judged(lines, events=[*log(), ev("lease.renewed")])
    assert outcomes(v)["heartbeat"] == S.FAILED


def test_a_heartbeat_that_renewed_nothing_is_not_one():
    dead = tool("ddflow_ddflow_heartbeat", out='{"renewed":false,"waiters":[]}', id=ITEM)
    lines = [dead if "heartbeat" in line else line for line in GOOD]
    assert S.read_stream(lines).calls[3].status == S.REFUSED
    assert outcomes(judged(lines))["heartbeat"] == S.FAILED


def test_no_commit_ahead_fails_and_an_unread_count_is_unavailable():
    assert outcomes(judged(commits=0))["commit"] == S.FAILED
    v = judged(commits=None)
    assert outcomes(v)["commit"] == S.UNAVAILABLE
    assert v.outcome == S.UNAVAILABLE


def test_an_edit_needs_a_write_that_worked():
    lines = [line for line in GOOD if '"write"' not in line]
    assert outcomes(judged(lines))["edit"] == S.FAILED


def test_completing_its_own_item_is_not_stopping():
    v = judged(events=[*log(), ev("item.completed")])
    assert outcomes(v)["stop"] == S.FAILED


def test_work_after_the_lease_refusal_is_not_stopping():
    turn = json.dumps({"type": "step_start"})
    gone = tool("write", "error", error=f"lane {LANE} holds no live lease: stop")
    after = tool("bash", out="", command="echo x > hello.txt")
    assert outcomes(judged([*GOOD, turn, gone]))["stop"] == S.PASSED
    assert outcomes(judged([*GOOD, turn, gone, turn, after]))["stop"] == S.FAILED


def test_a_call_sent_with_the_refused_one_was_already_in_flight():
    """Live: one turn held a refused write and a `sleep`; the model had not seen the refusal."""
    turn = json.dumps({"type": "step_start"})
    gone = tool("write", "error", error=f"lane {LANE} holds no live lease: stop")
    sent = tool("bash", out="", command="sleep 6")
    assert outcomes(judged([*GOOD, turn, gone, sent]))["stop"] == S.PASSED


def test_work_after_a_heartbeat_that_renewed_nothing_is_not_stopping():
    turn = json.dumps({"type": "step_start"})
    dead = tool("ddflow_ddflow_heartbeat", out='{"renewed":false}', id=ITEM)
    after = tool("bash", out="", command="ls")
    assert outcomes(judged([*GOOD, turn, dead]))["stop"] == S.PASSED
    assert outcomes(judged([*GOOD, turn, dead, turn, after]))["stop"] == S.FAILED


def test_going_past_the_brief_fails_even_when_the_exit_was_not_seen():
    v = judged(events=[*log(), ev("item.completed")], exit_code=None)
    assert outcomes(v)["stop"] == S.FAILED and v.outcome == S.FAILED


def test_a_lane_killed_or_failed_did_not_stop():
    assert outcomes(judged(exit_code=1))["stop"] == S.FAILED
    assert outcomes(judged(exit_code=None))["stop"] == S.UNAVAILABLE


def test_a_failure_outranks_unavailable():
    assert judged(commits=None, events=[]).outcome == S.FAILED


# --- several runs ----------------------------------------------------------------------------


def test_majority_of_runs():
    assert S.majority([S.PASSED, S.PASSED, S.FAILED]) == S.PASSED
    assert S.majority([S.PASSED, S.FAILED, S.FAILED]) == S.FAILED


def test_unavailable_is_never_a_vote_for_passed():
    assert S.majority([S.PASSED, S.UNAVAILABLE, S.UNAVAILABLE]) == S.UNAVAILABLE
    assert S.majority([]) == S.UNAVAILABLE


def test_unavailable_runs_weigh_the_same_on_both_sides():
    assert S.majority([S.PASSED, S.PASSED, S.UNAVAILABLE, S.UNAVAILABLE]) == S.PASSED
    assert S.majority([S.FAILED, S.FAILED, S.UNAVAILABLE, S.UNAVAILABLE]) == S.FAILED


def test_too_few_judged_runs_decide_nothing():
    assert S.majority([S.FAILED, S.UNAVAILABLE, S.UNAVAILABLE]) == S.UNAVAILABLE
    many = [S.PASSED, S.PASSED, S.FAILED, *[S.UNAVAILABLE] * 4]
    assert S.majority(many) == S.UNAVAILABLE


def test_a_tie_is_not_a_pass():
    assert S.majority([S.PASSED, S.FAILED]) == S.FAILED
