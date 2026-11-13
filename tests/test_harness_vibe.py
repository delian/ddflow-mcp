"""Mistral Vibe descriptor (VB-descriptor): documented facts, the three documented gaps
recorded as absent or unverified, and the harness list row."""

from __future__ import annotations

from ddflow.services import harnessfacts
from ddflow.services import harnessreg as HR

H = HR.get("vibe")


def test_descriptor_states_documented_facts_with_the_research_record():
    assert H.mcp.path == ".vibe/config.toml" and H.mcp.shape == "toml.mcp_servers"
    assert H.instructions is not None and H.instructions.agents_md == "yes"
    assert H.instructions.claude_md == "nv"
    assert H.hooks is not None and H.hooks.style == "vibe"
    assert H.hooks.file == ".vibe/hooks.toml" and H.hooks.block == "json"
    assert H.hooks.events == {"pre_tool": "pre_tool", "post_tool": "post_tool"}
    assert H.verified.record == "R-mistral-vibe"


def test_the_session_hooks_gap_is_recorded_not_guessed():
    # no session-start or session-end hook, no stable session id: never invented
    assert "session_start" not in H.hooks.events
    assert "session_end" not in H.hooks.events
    # the documented absence lives in the facts note, not in the unverified list (which
    # holds only facts still NOT VERIFIED -- a documented absence is a verified fact).
    assert "no session-start or session-end hook" in H.facts["hooks.events"]["note"]


def test_headless_is_documented_and_names_a_profile():
    assert H.headless is not None
    assert H.headless.argv == ("vibe", "-p")
    assert H.headless.resume_flag == "--resume" and H.headless.autonomy == "auto-approve"
    assert H.autonomy is not None and H.autonomy.profiles["auto-approve"] == "--auto-approve"


def test_documented_absences_and_unverified_are_distinct():
    # a documented absence is a fact in [verified].unverified, never a fabricated value:
    # CLAUDE.md is "nv" (not verified) because the README documents AGENTS.md, not CLAUDE.md.
    assert H.instructions.claude_md == "nv"
    assert H.commands is None  # commands are not documented, so the section is left out


def test_facts_overlay_marks_documented_and_lists_unverified():
    rows = {r["fact"]: r for r in harnessfacts.rows(H)}
    assert rows["hooks.events"]["status"] == "documented"
    assert rows["headless.argv"]["status"] == "documented"
    assert "hooks.session_id" not in rows  # no session id field documented
    assert any("session storage path" in u for u in H.verified.unverified)
