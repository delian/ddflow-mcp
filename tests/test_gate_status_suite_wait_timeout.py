"""Bc53afb94b4: `ddflow gate status` rendered a finished suite-lock timeout as a live queue entry.

The `SuiteBusy` / `ci run` reason -- "blocked on pid ... since ... you are #k in the queue" -- is
present tense and carries no timestamp, so a gate recorded UNAVAILABLE long ago reads as a wait
that is still in progress. The note must say when the outcome was recorded, that it is a PAST
timeout, and that the gate only needs re-running.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.services.gates.status_notes import _why_notes

REASON = "blocked on pid 989929 / PS-snapshot of /repo since 02:12:11, you are #2 in the queue"
AT = "2026-11-05T02:12:11.000000Z"


def _ci(reason: str, at: str = AT, outcome: str = "unavailable") -> NS:
    return NS(gates={"ci": NS(reason=reason, at=at, evidence={})})


def test_a_suite_wait_timeout_reads_as_a_past_timeout_with_its_recorded_time():
    note = _why_notes(_ci(REASON), [("ci", "unavailable")])["ci"]
    assert "recorded 2026-11-05 02:12" in note, note
    assert "a past timeout" in note, note
    assert "re-run the gate" in note, note


def test_the_reason_keeps_who_held_it_and_the_queue_position():
    note = _why_notes(_ci(REASON), [("ci", "unavailable")])["ci"]
    assert "blocked on pid 989929" in note, note
    assert "you are #2 in the queue" in note, note


def test_an_unknown_holder_time_is_still_a_past_timeout():
    reason = "blocked on pid 7 since an unknown time, you are #1 in the queue"
    note = _why_notes(_ci(reason), [("ci", "unavailable")])["ci"]
    assert "a past timeout" in note, note
    assert "re-run the gate" in note, note


def test_a_record_without_a_timestamp_says_past_without_a_time():
    note = _why_notes(_ci(REASON, at=""), [("ci", "unavailable")])["ci"]
    assert "a past timeout" in note, note
    assert "re-run the gate" in note, note
    assert "recorded" not in note, note


def test_a_recorded_time_outside_the_lock_reason_is_untouched():
    note = _why_notes(_ci("pre-commit is not installed"), [("ci", "unavailable")])["ci"]
    assert note == "pre-commit is not installed"


def test_a_failed_gates_reason_is_untouched():
    note = _why_notes(_ci("1 test failed: tests/test_x.py", outcome="failed"), [("ci", "failed")])[
        "ci"
    ]
    assert note == "1 test failed: tests/test_x.py"


def test_a_long_holder_reason_cuts_the_reason_not_the_past_timeout_note():
    """A holder line can embed a long path, pushing the note past WHY_CHARS: the past-timeout
    note must survive the truncation, or a long path drops it and re-lives the misread."""
    from ddflow.services.gates.reasons import WHY_CHARS

    path = "some_very_long_checkout_directory_name" * 6  # a reason past the cap on its own
    reason = f"blocked on pid 989929 / {path} since 02:12:11, you are #2 in the queue"
    note = _why_notes(_ci(reason), [("ci", "unavailable")])["ci"]
    assert len(note) <= WHY_CHARS, len(note)
    assert note.endswith("a past timeout -- re-run the gate)"), note
    assert "recorded 2026-11-05 02:12" in note, note


def test_a_reason_with_a_newline_before_the_time_is_still_a_past_timeout():
    """`split()` folds the newline out of the shown text, so the raw reason's line break must
    not stop the past-timeout note (a reason recorded across a line reads live otherwise)."""
    reason = (
        "blocked on pid 989929 / PS-snapshot of /repo since\r\n02:12:11, you are #2 in the queue"
    )
    note = _why_notes(_ci(reason), [("ci", "unavailable")])["ci"]
    assert "a past timeout" in note, note
    assert "re-run the gate" in note, note
