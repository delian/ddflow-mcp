"""`clock.parse_duration`, the trigger `_s` aliases and the one wait cap (B-uni-clock.3)."""

from __future__ import annotations

import importlib

import pytest

from ddflow.core import clock
from ddflow.services import triggers as TR
from ddflow.surfaces.tools import _common as MC

# `ddflow.api.lifecycle.wait` is also the name of the function the package re-exports.
W = importlib.import_module("ddflow.api.lifecycle.wait")


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("90", 90),
        ("90s", 90),
        ("30m", 1800),
        ("1.5h", 5400),
        ("1h30m", 5400),
        ("2d", 172800),
        ("1w", 604800),
        (" 5 M ", 300),
        ("0", 0),
        (45, 45),
        (0.5, 0.5),
    ],
)
def test_a_duration_is_seconds(text, seconds):
    assert clock.parse_duration(text) == seconds


def test_a_bare_number_takes_the_unit_asked_for():
    assert clock.parse_duration(10, unit="m") == 600
    assert clock.parse_duration("10", unit="h") == 36000
    assert clock.parse_duration("10s", unit="h") == 10  # a written unit wins


@pytest.mark.parametrize(
    "bad",
    ["", " ", "m", "-5", "5x", "1h30", "1h 30", "5 min", "abc", "1..5s", True, None, -1, float("nan"),
     float("inf"), [30], 10**400, "9" * 400],
)  # fmt: skip
def test_anything_else_is_refused_not_read_as_zero(bad):
    with pytest.raises(ValueError):
        clock.parse_duration(bad)


def test_an_unknown_default_unit_is_refused():
    with pytest.raises(ValueError):
        clock.parse_duration(1, unit="y")


def _spec(**more):
    return {"event": "gate.failed", "action": {"job": "j"}, **more}


def test_the_seconds_form_is_the_same_definition_as_the_minutes_form():
    a, ea = TR.build("t", _spec(window=30, debounce=10, cooldown=60))
    b, eb = TR.build("t", _spec(window_s=1800, debounce_s="10m", cooldown_s=3600))
    assert ea == eb == []
    assert (b.window, b.debounce, b.cooldown) == (30, 10, 60)
    assert all(isinstance(v, int) for v in (b.window, b.debounce, b.cooldown))
    assert a.digest() == b.digest()


def test_a_fraction_of_a_minute_is_kept():
    t, errors = TR.build("t", _spec(debounce_s=90))
    assert errors == []
    assert t.debounce == 1.5


def test_a_knob_and_its_alias_together_are_refused():
    t, errors = TR.build("t", _spec(window=30, window_s=1800))
    assert t is None
    assert any("window" in e and "not both" in e for e in errors)


@pytest.mark.parametrize("bad", ["soon", -1, True, "5x"])
def test_a_bad_alias_is_refused_naming_the_knob(bad):
    t, errors = TR.build("t", _spec(cooldown_s=bad))
    assert t is None
    assert any("cooldown_s" in e for e in errors)


def test_the_minute_keys_still_refuse_a_fraction():
    t, errors = TR.build("t", _spec(window=1.5))
    assert t is None
    assert any("whole number" in e for e in errors)


def test_the_cluster_window_honours_a_fraction_of_a_minute():
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    evs = [(t0 + timedelta(seconds=s), s) for s in (0, 45, 90)]
    assert len(TR.cluster(evs, 2, 0.75)) == 2  # 45s apart: inside a 45s window
    assert TR.cluster(evs, 3, 0.75) == []  # the three span 90s


def test_one_cap_for_the_cli_and_for_mcp():
    assert MC.MCP_WAIT_MAX_S == W.WAIT_MAX_S == 1800


def test_a_wait_longer_than_the_cap_is_shortened_and_says_so(repo, monkeypatch):
    """Blocked for ever on a fake clock that advances 100 s a reading: only the cap ends it."""
    seen: list[str] = []
    now = [0.0]

    def tick():
        now[0] += 100.0
        return now[0]

    blocked = {"status": "blocked", "why": "x", "ready": [], "waiting_on": [], "blocked": []}
    monkeypatch.setattr(W, "_judge_wait", lambda *a, **k: blocked)
    monkeypatch.setattr(W.time, "monotonic", tick)
    monkeypatch.setattr(W.time, "sleep", lambda _s: None)
    out = W.wait(repo, item="", timeout_s=10**6, poll_s=1, on_progress=seen.append)
    assert any(str(W.WAIT_MAX_S) in m and "ask again" in m for m in seen), seen
    assert out.data["waited_s"] <= W.WAIT_MAX_S + 300, out.data["waited_s"]


def test_a_wait_within_the_cap_says_nothing_about_it(repo):
    seen: list[str] = []
    W.wait(repo, item="", timeout_s=0, poll_s=1, on_progress=seen.append)
    assert not any("ask again" in m for m in seen)


def test_a_huge_alias_is_reported_not_raised(tmp_path):
    t, errors = TR.build("t", _spec(window_s=10**400))
    assert t is None
    assert any("window_s" in e for e in errors)


def test_the_cap_notice_is_only_given_to_a_caller_that_will_wait(repo):
    seen: list[str] = []
    out = W.wait(repo, item="", timeout_s=10**6, poll_s=1, on_progress=seen.append)
    assert out.exit == 2  # nothing to wait for: it answers at once
    assert not any("ask again" in m for m in seen), seen


def test_the_tool_table_does_not_pull_in_the_api():
    import subprocess
    import sys

    code = "import sys, ddflow.surfaces.tools._common; print('ddflow.api' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


def test_an_mcp_timeout_beyond_a_double_is_capped_not_raised():
    assert MC._wait_timeout({"timeout": 10**400}) == MC.MCP_WAIT_MAX_S
    assert MC._wait_timeout({"timeout": 12}) == 12.0
    assert MC._wait_timeout({}) == MC.MCP_WAIT_DEFAULT_S


def test_an_mcp_timeout_given_as_text_is_still_read_as_a_number():
    assert MC._wait_timeout({"timeout": "120"}) == 120.0
    assert MC._wait_timeout({"timeout": "99999"}) == MC.MCP_WAIT_MAX_S
