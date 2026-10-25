"""An inject event a descriptor declares but whose hook handler emits nothing is reported
'declared, not delivering', and a harness whose only inject event is such a door still gets
the first-call preamble (bug B1878cc0da3)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import firstcall
from ddflow.api import harness as H
from ddflow.services import harnessreg as HR

#: The shipped harnesses whose only declared injection is the prompt door, through which
#: ddflow's prompt handler emits nothing: they have no hook-delivered start at all.
PROMPT_ONLY = ("antigravity", "cline")


def test_a_declared_prompt_injection_of_a_silent_handler_is_reported_non_delivering(repo):
    for hid in PROMPT_ONLY:
        h = HR.get(hid)
        assert h.hooks is not None and "prompt" in h.hooks.inject
        assert H.event_level(h, "prompt") == H.DECLARED
        assert "declared, not delivering" in H.harness_show(repo, hid).data["message"]


def test_claude_s_declared_prompt_is_reported_non_delivering_too(repo):
    # claude.toml declares inject=['session_start', 'prompt']: the prompt half is exactly the
    # door that emits nothing, and its show line must say so (bug B1878cc0da3).
    claude = HR.get("claude")
    assert H.event_level(claude, "prompt") == H.DECLARED
    assert "declared, not delivering" in H.harness_show(repo, "claude").data["message"]


def test_a_wired_event_the_descriptor_never_declared_is_reported_wired_not_injected(repo):
    # cline wires agent_start (its session_start) but declares inject=['prompt'] only: the
    # wired door never puts ddflow's output before the model, and show must say so.
    assert H.event_level(HR.get("cline"), "session_start") == H.WIRED


def test_an_inject_event_whose_handler_emits_stays_reported_delivering(repo):
    assert H.event_level(HR.get("claude"), "session_start") == H.NATIVE
    assert H.event_level(HR.get("gemini"), "session_start") == H.NATIVE
    # A wired pre_tool can refuse the armed call: it stays a native hook even without inject.
    assert H.event_level(HR.get("gemini"), "pre_tool") == H.NATIVE


def test_a_prompt_only_injecting_harness_gets_the_first_call_preamble(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_SESSION_FIRST_CALL", "true")
    for hid in PROMPT_ONLY:
        assert firstcall.hook_delivers(repo, hid) is False
        note = firstcall.preamble(repo, hid, "ddflow_status", {"name": hid})
        assert note and "ddflow session start" in note


def test_a_harness_whose_native_hook_injects_the_start_gets_no_preamble(repo, monkeypatch):
    from ddflow.services import hookwriters as HW

    monkeypatch.setenv("DDFLOW_SESSION_FIRST_CALL", "true")
    HW.apply(repo, HR.get("claude"), "install")
    assert firstcall.hook_delivers(repo, "claude") is True
    note = firstcall.preamble(repo, "claude", "ddflow_status", {"name": "claude-code"})
    assert not note
