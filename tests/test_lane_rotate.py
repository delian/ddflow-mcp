"""The lane-rotation hint (TE-context-caps): it fires only at a measured context past
``[session].agent_rotate_tokens``, stays silent when the usage is unknown, and maps the
auto-compact window knobs to the Claude Code environment names the docs spell."""

from __future__ import annotations

from ddflow.config import Config
from ddflow.services import lanerotate as LR


def _cfg(**kw) -> Config:
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg.session, k, v)
    return cfg


# -- the rotate hint ----------------------------------------------------------


def test_hint_is_silent_below_the_threshold():
    cfg = _cfg(agent_rotate_tokens=250000)
    assert LR.hint(249999, cfg) == ""


def test_hint_fires_at_the_threshold_from_fixture_usage():
    # The fixture usage is the number the hint compares, exactly as a transcript reader
    # (B-am-ingest) or the lane ledger (B-gap-spend) would hand ddflow once it lands.
    usage = 250000
    text = LR.hint(usage, _cfg(agent_rotate_tokens=250000))
    assert "rotate" in text
    assert "250000" in text
    assert "ddflow brief" in text  # the fresh agent's first call, named in the action


def test_hint_fires_past_the_threshold():
    assert LR.hint(260000, _cfg(agent_rotate_tokens=250000)) != ""


def test_hint_is_silent_when_usage_is_unknown():
    # DDflow never guesses a context it did not measure: None is not past anything.
    assert LR.hint(None, _cfg(agent_rotate_tokens=250000)) == ""


def test_hint_is_silent_when_off():
    assert LR.hint(400000, _cfg(agent_rotate_tokens=0)) == ""


def test_a_measured_context_is_not_rotating_when_unknown_or_off():
    assert LR.rotating(None, _cfg(agent_rotate_tokens=250000)) is False
    assert LR.rotating(250000, _cfg(agent_rotate_tokens=0)) is False
    assert LR.rotating(250000, _cfg(agent_rotate_tokens=250000)) is True


# -- the hand-off note --------------------------------------------------------


def test_handoff_note_names_the_item_and_next_step():
    note = LR.handoff_note("TE-context-caps", "implemented the rotate hint, two gates left")
    assert "TE-context-caps" in note
    assert "ddflow brief" in note
    assert len(note) <= LR.HANDOFF_NOTE_CHARS


def test_handoff_note_round_trips_a_long_summary_bounded():
    # The note the rotated agent leaves becomes the fresh agent's brief; it is bounded so it
    # round-trips through `ddflow brief` instead of dragging the transcript along with it.
    long = "word " * 400
    note = LR.handoff_note("TE-context-caps", long)
    assert len(note) <= LR.HANDOFF_NOTE_CHARS
    assert note.endswith("`ddflow brief`.")


def test_handoff_note_without_a_summary_still_names_the_next_step():
    assert LR.handoff_note("TE-context-caps", "   ").startswith("rotated: TE-context-caps")


def test_handoff_note_stays_under_the_cap_even_with_a_long_item_id():
    # Finding #2 (grok rubber_duck): a long item id leaves no room, and the ellipsis was
    # laid on AFTER the budget, pushing the note past the cap. The whole note is capped.
    note = LR.handoff_note("x" * 400, "summary")
    assert len(note) <= LR.HANDOFF_NOTE_CHARS


# -- the auto-compact window knobs -------------------------------------------


def test_window_env_maps_the_knobs_to_the_documented_names():
    assert LR.window_env(_cfg()) == {}
    env = LR.window_env(_cfg(claude_autocompact_window=500000, claude_autocompact_pct_override=80))
    assert env[LR.AUTO_COMPACT_WINDOW_ENV] == "500000"
    assert env[LR.AUTO_COMPACT_PCT_ENV] == "80"


# -- the doctor notes ---------------------------------------------------------


def test_doctor_notes_are_silent_in_the_default_state():
    # All defaults: no measured context and no window chosen -> no note, so the every-run
    # report is not spammed with advice nobody asked for.
    assert LR.doctor_notes(_cfg()) == []


def test_doctor_notes_name_the_window_alternative_when_configured():
    notes = LR.doctor_notes(_cfg(claude_autocompact_window=400000))
    assert any("auto-compact window" in n for n in notes)


def test_doctor_notes_fire_the_hint_when_past_the_threshold():
    notes = LR.doctor_notes(_cfg(agent_rotate_tokens=250000), context_tokens=300000)
    assert any("rotate" in n for n in notes)
