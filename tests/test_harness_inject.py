"""An inject event a descriptor declares but whose hook handler emits nothing is reported
'declared, not delivering', and a harness whose only inject event is such a door still gets
the first-call preamble (bug B1878cc0da3)."""

from __future__ import annotations

import sys
from dataclasses import replace
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
    # cline wires agent_end (its stop) but declares inject=['prompt'] only: the wired door
    # never puts ddflow's output before the model, and show must say so.
    assert H.event_level(HR.get("cline"), "stop") == H.WIRED
    assert "wired, not injected" in H.harness_show(repo, "cline").data["message"]


def test_a_wired_start_the_hook_does_not_inject_falls_back_the_ladder(repo):
    # A session start a wired hook does not inject is delivered the fallback way, and the
    # first-call preamble agrees with it (firstcall.hook_delivers): the ladder's next rung
    # for these plugin-bearing harnesses, not 'nothing is shown'.
    from ddflow.api import firstcall

    cline = HR.get("cline")
    assert "session_start" in cline.hooks.events and "session_start" not in cline.hooks.inject
    assert firstcall.hook_delivers(repo, "cline") is False
    assert H.event_level(cline, "session_start") == H.PLUGIN


def test_every_shipped_injected_session_start_uses_a_delivering_emitter(repo):
    # NATIVE for an injected session_start is true only when the emitter really renders the
    # brief for that agent's dialect; a _silent or _antigravity-shaped emitter renders the
    # neutral reply ("" or "{}") and would claim a delivery nothing travels through.
    from ddflow.services import hookio

    guard = [
        h.id
        for h in HR.load()
        if h.hooks is not None
        and h.hooks.style in HR.COMMAND_HOOK_STYLES
        and "session_start" in h.hooks.inject
    ]
    assert guard, "no shipped descriptor declares session_start in inject"
    for hid in guard:
        plan = hookio.plan_for(hid)
        out = hookio.emit(plan, "session_start", "probe").strip()
        assert plan is not None and out and out != "{}"


def test_an_inject_event_whose_handler_emits_stays_reported_delivering(repo):
    assert H.event_level(HR.get("claude"), "session_start") == H.NATIVE
    assert H.event_level(HR.get("gemini"), "session_start") == H.NATIVE
    # claude's PreToolUse dialect is one ddflow can deny in: a wired pre_tool there is a
    # native hook even without inject; gemini's emitter has no deny reply, so its wired
    # pre_tool fires but shows the model nothing.
    assert H.event_level(HR.get("claude"), "pre_tool") == H.NATIVE
    assert H.event_level(HR.get("gemini"), "pre_tool") == H.WIRED


def test_pre_tool_level_derives_from_the_deny_dialect(repo):
    # NATIVE only claims what the enforcement really does: the hookio deny reply exists for
    # the claude and codex emitter dialects (DENY_EMITTERS); every other emitter's firing
    # records 'not enforceable' and reaches the model not at all, whatever block mode says.
    gem = HR.get("gemini")
    claude_dialect = replace(gem, hooks=replace(gem.hooks, emitter="claude"))
    assert claude_dialect.hooks.emitter == "claude"
    assert "pre_tool" not in claude_dialect.hooks.inject
    assert H.event_level(claude_dialect, "pre_tool") == H.NATIVE
    assert "pre_tool" not in gem.hooks.inject
    assert H.event_level(gem, "pre_tool") == H.WIRED
    injected_gem = replace(gem, hooks=replace(gem.hooks, inject=("session_start", "prompt", "pre_tool")))
    assert H.event_level(injected_gem, "pre_tool") == H.DECLARED
    injected_claude = replace(gem, hooks=replace(claude_dialect.hooks, inject=("session_start", "prompt", "pre_tool")))
    assert H.event_level(injected_claude, "pre_tool") == H.NATIVE
    # The session_start claim reads the SAME field: a copy whose emitter turns non-claude
    # must not be judged by the registry's descriptor; its pre_tool flips to WIRED and its
    # injected session_start drops to DECLARED from this copy alone.
    copied = replace(claude_dialect, hooks=replace(claude_dialect.hooks, inject=("session_start", "prompt")))
    inverted = replace(copied, hooks=replace(copied.hooks, emitter="gemini"))
    assert H.event_level(inverted, "session_start") == H.NATIVE
    assert H.event_level(inverted, "pre_tool") == H.WIRED


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
