"""The launch brief is sized to the session it starts (LS-launch-brief-budget, P-lean-start).

Today the brief is capped at `[session].agent_brief_max_chars` (9,000 chars, ~2.1K tokens) whatever
the model's window. A small window leaves no room for that, so the cap follows the start budget
(`context_budget_tokens`, or the agent type's entry in `[session.context_budget]`): a share of it,
never above the fixed ceiling, never below a floor. Under a lean threshold the standing sections the
gates enforce anyway (tests, reviews, gates, filing, the project's own text) and the lessons are
left out, with one line saying where the rest is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import _KNOB_CHECKS, Config
from ddflow.services import launchbrief as LB


def _session(**kw):
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg.session, k, v)
    return cfg.session


def test_the_default_budget_leaves_the_fixed_ceiling_alone():
    s = LB.sizing(_session(), "")
    assert (s.cap, s.lean) == (9000, False)


def test_a_smaller_budget_gives_a_smaller_cap_down_to_the_floor():
    # share 0.1 of the budget, 4 bytes a token
    assert LB.sizing(_session(context_budget_tokens=16000), "").cap == 6400
    assert LB.sizing(_session(context_budget_tokens=12000), "").cap == 4800
    assert LB.sizing(_session(context_budget_tokens=8000), "").cap == 4000, "the floor, not 3200"


def test_the_fixed_ceiling_still_caps_a_large_budget():
    assert LB.sizing(_session(context_budget_tokens=200000), "").cap == 9000


def test_no_budget_means_the_fixed_ceiling_and_no_ceiling_means_the_derived_cap():
    assert LB.sizing(_session(context_budget_tokens=0), "").cap == 9000
    s = _session(context_budget_tokens=200000, agent_brief_max_chars=0)
    assert LB.sizing(s, "").cap == 80000, "ceiling 0 = none: the share alone decides"
    assert LB.sizing(_session(context_budget_tokens=0, agent_brief_max_chars=0), "").cap == 0


def test_lean_starts_at_its_threshold():
    assert not LB.sizing(_session(context_budget_tokens=16000), "").lean  # 6400 > 6000
    assert LB.sizing(_session(context_budget_tokens=14000), "").lean  # 5600
    assert (
        LB.sizing(_session(context_budget_tokens=14000, agent_brief_lean_chars=0), "").lean is False
    )


def test_an_agent_type_budget_sizes_the_brief_for_that_type():
    s = _session(context_budget={"claude": 8000})
    assert LB.sizing(s, "claude").cap == 4000 and LB.sizing(s, "claude").lean
    assert LB.sizing(s, "codex").cap == 9000 and not LB.sizing(s, "codex").lean


@pytest.mark.parametrize(
    ("knob", "bad"),
    [
        ("session.agent_brief_budget_share", 0),
        ("session.agent_brief_budget_share", 1.5),
        ("session.agent_brief_budget_share", True),
        ("session.agent_brief_min_chars", -1),
        ("session.agent_brief_lean_chars", -1),
    ],
)
def test_the_knobs_refuse_what_cannot_size_a_brief(knob, bad):
    assert _KNOB_CHECKS[knob](bad)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "Widen the widget",
        "--body",
        "Make the widget wider without touching the gadget.",
        "--globs",
        "src/widget.py",
    )
    return repo


def _brief(repo):
    code, out, err = run_cli(repo, "brief", "--for-agent", "sub-1", "T1")
    assert code == 0, err
    return out


def test_the_default_brief_is_the_full_one(proj):
    out = _brief(proj)
    for sec in ("## Tests", "## Reviews", "## Gates", "## Filing", "## Report"):
        assert sec in out, sec
    assert "Lean brief" not in out


def test_a_small_budget_renders_the_lean_brief_with_a_pointer(proj):
    run_cli(proj, "config", "--set", "session.context_budget_tokens", "8000")
    out = _brief(proj)
    assert (
        "Lean brief" in out and "`ddflow_help`" in out and "lessons" in out.split("Lean brief")[1]
    )
    for gone in ("## Tests", "## Reviews", "## Gates", "## Filing", "## This project"):
        assert gone not in out, gone
    for kept in ("## Start", "## Finish", "## Never", "## Report"):
        assert kept in out, kept
    # what the lean brief still carries: identity, item, the lease, merge and complete, the report
    for must in (
        "`T1`",
        "Widen the widget",
        "ddflow heartbeat T1",
        "ddflow claim T1",
        "--agent sub-1",
    ):
        assert must in out, must
    assert len(out) < 5000, len(out)
    run_cli(proj, "config", "--set", "session.context_budget_tokens", "40000")
    full_len = len(_brief(proj))
    assert full_len > len(out) + 500


def test_the_running_agent_type_picks_its_own_budget(proj, monkeypatch):
    run_cli(proj, "config", "--set", "session.context_budget_tokens", "40000")
    cfgp = proj / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nclaude = 8000\n")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-1")
    assert "Lean brief" in _brief(proj)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert "Lean brief" not in _brief(proj)


def test_a_section_the_project_turned_off_stays_off_in_both_modes(proj):
    run_cli(proj, "config", "--set", "session.agent_brief_off", "report")
    assert "## Report" not in _brief(proj) and "report" not in LB.LEAN_OFF
    run_cli(proj, "config", "--set", "session.context_budget_tokens", "8000")
    lean = _brief(proj)
    assert "## Report" not in lean and "Lean brief" in lean
