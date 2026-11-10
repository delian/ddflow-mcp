"""The effective Claude Code subagent cache TTL (TE-subagent-ttl): the documented precedence
matrix, the version gate and the doctor warning while subagents run on the 5-minute TTL.

Precedence (docs: agent-token-features.md 2.1, token-spend.md 5):
``FORCE_PROMPT_CACHING_5M`` > ``CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`` (env) >
``subagentPromptCacheTtl`` (setting) > ``ENABLE_PROMPT_CACHING_1H`` > the 5m default. The
main-conversation knobs do not govern subagents and are deliberately left out.
"""

from __future__ import annotations

from pathlib import Path

import ddflow.services.cachettl as CTL


# -- version parsing ---------------------------------------------------------


def test_version_is_parsed_from_claude_output():
    assert CTL.parse_version("2.1.318 (Claude Code)") == (2, 1, 318)


def test_version_unknown_when_unparseable():
    assert CTL.parse_version("") is None
    assert CTL.parse_version("no version here") is None
    assert CTL.parse_version(None) is None


def test_one_hour_support_needs_2_1_242():
    assert CTL.supports_1h("2.1.241") is False
    assert CTL.supports_1h("2.1.242") is True
    assert CTL.supports_1h("2.1.318") is True
    assert CTL.supports_1h("junk") is None


# -- the precedence matrix ---------------------------------------------------


def test_default_is_five_minutes_even_without_any_knob():
    v = CTL.effective_subagent_ttl({}, {}, "2.1.300")
    assert v.ttl == "5m"
    assert v.source == "default"
    assert v.on_five_minutes
    assert v.one_hour_available is True


def test_force_projection_overrides_an_hour_knob():
    v = CTL.effective_subagent_ttl(
        {"FORCE_PROMPT_CACHING_5M": "1", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h"},
        {},
        "2.1.300",
    )
    assert v.ttl == "5m"
    assert v.source == "forced_5m"
    assert v.forced


def test_subagent_env_wins_over_settings_and_enable():
    v = CTL.effective_subagent_ttl(
        {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h"},
        {"subagentPromptCacheTtl": "5m"},
        "2.1.300",
    )
    assert v.ttl == "1h"
    assert v.source == "subagent_env"


def test_subagent_env_five_minutes_is_still_five_minutes():
    v = CTL.effective_subagent_ttl(
        {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "5m"}, {}, "2.1.300"
    )
    assert v.ttl == "5m"
    assert v.source == "subagent_env"


def test_subagent_setting_applies_when_env_unset():
    v = CTL.effective_subagent_ttl({}, {"subagentPromptCacheTtl": "1h"}, "2.1.300")
    assert v.ttl == "1h"
    assert v.source == "subagent_setting"


def test_enable_1h_raises_the_default_to_an_hour():
    v = CTL.effective_subagent_ttl({"ENABLE_PROMPT_CACHING_1H": "1"}, {}, "2.1.300")
    assert v.ttl == "1h"
    assert v.source == "enable_1h"


def test_enable_1h_does_not_override_a_forced_five_minutes():
    v = CTL.effective_subagent_ttl(
        {"FORCE_PROMPT_CACHING_5M": "true", "ENABLE_PROMPT_CACHING_1H": "1"}, {}, "2.1.300"
    )
    assert v.ttl == "5m"
    assert v.forced


def test_enable_1h_only_truthy_values_enable():
    for off in ("", "0", "false", "no"):
        assert CTL.effective_subagent_ttl({"ENABLE_PROMPT_CACHING_1H": off}, {}, "2.1.300").ttl == "5m"


def test_main_conversation_ttl_env_does_not_govern_subagents():
    # CLAUDE_CODE_PROMPT_CACHE_TTL is the MAIN knob; subagents stay on the 5m default.
    v = CTL.effective_subagent_ttl({"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}, {}, "2.1.300")
    assert v.ttl == "5m"


# -- version too old ---------------------------------------------------------


def test_too_old_version_reports_1h_unavailable():
    v = CTL.effective_subagent_ttl({}, {}, "2.1.100")
    assert v.ttl == "5m"
    assert v.one_hour_available is False


def test_unknown_version_is_neither_available_nor_a_pass():
    v = CTL.effective_subagent_ttl({}, {}, None)
    assert v.one_hour_available is None


# -- the doctor note ---------------------------------------------------------


def test_doctor_note_warns_on_five_minutes(monkeypatch, tmp_path):
    monkeypatch.delenv(CTL.SUBAGENT_TTL_ENV, raising=False)
    monkeypatch.delenv(CTL.ENABLE_1H_ENV, raising=False)
    monkeypatch.delenv(CTL.FORCE_5M_ENV, raising=False)
    monkeypatch.setattr(CTL, "claude_version", lambda: "2.1.300")
    monkeypatch.setattr(CTL, "read_settings", lambda repo: {})
    (note,) = CTL.doctor_notes(Path(tmp_path))
    assert note.startswith("subagent cache TTL: 5m")
    assert CTL.SUBAGENT_TTL_ENV in note


def test_doctor_note_quiet_on_one_hour(monkeypatch, tmp_path):
    monkeypatch.setenv(CTL.SUBAGENT_TTL_ENV, "1h")
    monkeypatch.setattr(CTL, "claude_version", lambda: "2.1.300")
    (note,) = CTL.doctor_notes(Path(tmp_path))
    assert note.startswith("subagent cache TTL: 1h")
    assert note.endswith("(via " + CTL.SUBAGENT_TTL_ENV + ")")


def test_doctor_note_names_upgrade_when_too_old(monkeypatch, tmp_path):
    monkeypatch.delenv(CTL.SUBAGENT_TTL_ENV, raising=False)
    monkeypatch.setattr(CTL, "claude_version", lambda: "2.0.0")
    monkeypatch.setattr(CTL, "read_settings", lambda repo: {})
    (note,) = CTL.doctor_notes(Path(tmp_path))
    assert "older than 2.1.242" in note
    assert "unavailable" in note
