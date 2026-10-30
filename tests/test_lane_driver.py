"""The Lane contract and the process-headless driver, against a fake agent that replays
recorded stdout and exit codes (tests/fixtures/lane_driver)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from ddflow.services import lane_driver as ld
from ddflow.services import lane_headless as lh
from ddflow.services.engines import limit_detect, stream_map
from ddflow.services.harnessv2 import Autonomy, Headless

FIX = Path(__file__).parent / "fixtures" / "lane_driver"
AGENT = str(FIX / "fake_agent.py")
RULES = stream_map.parse_rules(
    [
        {
            "when": {"path": "type", "equals": "session"},
            "emit": "started",
            "fields": {"session": "sessionId"},
        },
        {
            "when": {"path": "type", "equals": "tool_call"},
            "emit": "tool",
            "fields": {"name": "tool"},
        },
        {"when": {"path": "type", "equals": "env"}, "emit": "tool", "fields": {"name": "value"}},
        {"when": {"path": "type", "equals": "home"}, "emit": "tool", "fields": {"name": "home"}},
        {"when": {"path": "type", "equals": "stdin"}, "emit": "tool", "fields": {"name": "text"}},
        {
            "when": {"path": "usage.totalTokens", "exists": True},
            "emit": "usage",
            "fields": {"input": "usage.inputTokens"},
        },
        {"when": {"path": "reason.kind", "equals": "completed"}, "emit": "settled", "fields": {}},
    ]
)
LIMIT = limit_detect.parse({"exit_codes": [75], "messages": ["resets (?P<reset>at \\d+)"]})


def spec(*extra, rec="ok.jsonl", **kw):
    argv = (sys.executable, AGENT, str(FIX / rec), *extra)
    return ld.LaunchSpec(name="t", argv=argv, stream_rules=RULES, limit=LIMIT, stop_grace_s=2, **kw)


@pytest.fixture
def driver():
    return lh.ProcessHeadless()


def finish(driver, h, stop=True):
    evs = list(driver.events(h))
    deadline = time.time() + 10
    while driver.status(h).state == "running" and time.time() < deadline:
        time.sleep(0.02)
    st = driver.status(h)
    if stop:
        driver.stop(h)  # removes the private home; the lane has ended, so this only cleans up
    return evs, st


def test_replay_gives_canonical_events_and_status(driver):
    h = driver.start(spec(pass_env=("PATH",)))
    evs, st = finish(driver, h)
    assert [e.event for e in evs] == ["started", "tool", "usage", "settled"]
    assert [e.seq for e in evs] == [1, 2, 3, 4]
    assert evs[0].fields == {"session": "s-123"} and evs[0].lane == "t"
    assert st.state == "exited" and st.exit_code == 0 and st.session == "s-123"
    assert st.events == 4 and st.skipped == 2 and st.dropped == 0


def test_exit_code_meaning_and_failure(driver):
    s = spec("--exit", "3", exit_codes={"3": "auth"})
    _, st = finish(driver, driver.start(s))
    assert st.state == "failed" and st.exit_meaning == "auth"


def test_limit_detected_by_exit_code_and_by_message(driver):
    _, st = finish(driver, driver.start(spec("--exit", "75", rec="limit.jsonl")))
    assert st.limit == "exit_code"
    _, st = finish(
        driver,
        driver.start(spec("--exit", "1", "--stderr", "limit resets at 17", rec="limit.jsonl")),
    )
    assert (st.limit, st.reset) == ("message", "at 17")
    _, st = finish(driver, driver.start(spec("--exit", "1", rec="limit.jsonl")))
    assert st.limit == ""


def test_steer_is_unavailable_never_emulated(driver):
    h = driver.start(spec())
    r = driver.steer(h, "hello")
    assert r.status == "unavailable" and "steer" not in h.capabilities
    finish(driver, h)


def test_capabilities_follow_the_spec(driver):
    h = driver.start(spec())
    assert h.capabilities == {"interrupt", "usage", "tool_events"}
    finish(driver, h)
    h = driver.start(spec(resume=True))
    assert "resume" in h.capabilities
    finish(driver, h)
    h = driver.start(ld.LaunchSpec(name="q", argv=(sys.executable, AGENT, str(FIX / "ok.jsonl"))))
    assert h.capabilities == {"interrupt"}  # no stream rules: no usage or tool events claimed
    finish(driver, h)
    assert driver.capabilities <= ld.CAPABILITIES


def test_stop_kills_the_whole_process_group_and_removes_home(driver, tmp_path):
    pidfile = tmp_path / "child.pid"
    h = driver.start(spec("--hang", "--child", str(pidfile)))
    deadline = time.time() + 10
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.02)
    child = int(pidfile.read_text())
    home = Path(h.private[0])
    assert home.is_dir()
    assert driver.stop(h, grace_s=0.5).ok
    st = driver.status(h)
    assert st.state == "stopped"
    assert not home.exists()
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the grandchild outlived the group kill")


def test_interrupt_signals_the_group(driver):
    h = driver.start(spec("--hang"))
    assert next(driver.events(h, idle_s=20)).event == "started"  # the agent is up
    assert driver.interrupt(h).ok
    deadline = time.time() + 10
    while driver.status(h).state == "running" and time.time() < deadline:
        time.sleep(0.02)
    assert driver.status(h).state == "failed"
    driver.stop(h)


def test_keys_by_env_name_are_passed_but_never_shown_or_leaked(driver, monkeypatch):
    monkeypatch.setenv("MY_KEY_SRC", "sk-very-secret-value")
    monkeypatch.setenv("OTHER_SECRET", "must-not-reach-the-lane")
    h = driver.start(
        spec("--echo-env", "API_KEY", env={"API_KEY": "MY_KEY_SRC"}, pass_env=("PATH",))
    )
    evs, _ = finish(driver, h)
    assert [e.fields["name"] for e in evs if e.event == "tool"][:1] == ["[redacted]"]
    assert "sk-very-secret-value" not in repr(h) and "sk-very-secret-value" not in repr(evs)
    h2 = driver.start(spec("--echo-env", "OTHER_SECRET", pass_env=("PATH",)))
    evs, _ = finish(driver, h2)
    assert (
        next(e.fields["name"] for e in evs if e.event == "tool") == "<unset>"
    )  # only named variables reach the lane


def test_missing_key_env_refuses_before_launch(driver, monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    r = driver.start(spec(env={"API_KEY": "NOT_SET_ANYWHERE"}))
    assert r.status == "failed" and "NOT_SET_ANYWHERE" in r.detail and "sk" not in r.detail


def test_lane_private_home_and_fixed_telemetry_env(driver, tmp_path):
    home = tmp_path / "lane-home"
    h = driver.start(
        spec(
            "--echo-env",
            "DSH_TELEMETRY_MODE",
            "--pwd-home",
            home=home,
            home_env="DSH_HOME",
            fixed_env={"DSH_TELEMETRY_MODE": "DISABLED"},
            pass_env=("PATH",),
        )
    )
    evs, _ = finish(driver, h)
    names = [e.fields["name"] for e in evs if e.event == "tool"]
    assert "DISABLED" in names and str(home) in names
    assert oct(home.stat().st_mode & 0o777) == "0o700"
    assert home.exists()  # a home the caller named is not ours to delete


def test_prompt_goes_on_stdin_without_a_placeholder(driver):
    h = driver.start(spec("--read-stdin", prompt="do the thing"))
    evs, _ = finish(driver, h)
    assert any(e.fields.get("name") == "do the thing" for e in evs)


def test_prompt_file_placeholder_writes_a_private_file(driver, tmp_path):
    argv = (sys.executable, AGENT, str(FIX / "ok.jsonl"), "{prompt_file}")
    h = driver.start(ld.LaunchSpec(name="p", argv=argv, prompt="x", home=tmp_path / "h"))
    assert not isinstance(h, ld.Result)
    pf = tmp_path / "h" / "prompt.txt"
    assert pf.read_text() == "x" and oct(pf.stat().st_mode & 0o777) == "0o600"
    finish(driver, h)
    assert not pf.exists() and (tmp_path / "h").is_dir()  # the file goes, a caller's home stays


def test_nothing_in_the_spec_can_point_the_lane_at_the_real_home(driver, tmp_path):
    home = tmp_path / "h"
    h = driver.start(spec("--pwd-home", home=home, fixed_env={"HOME": "/home/real"}))
    evs, _ = finish(driver, h)
    assert [e.fields["name"] for e in evs if e.fields.get("name", "").startswith("/")] == [
        str(home)
    ]


def test_a_prompt_with_a_stale_prompt_file_value_still_reaches_stdin(driver):
    h = driver.start(spec("--read-stdin", prompt="hi", values={"prompt_file": "/stale"}))
    evs, _ = finish(driver, h)
    assert any(e.fields.get("name") == "hi" for e in evs)


def test_none_values_are_absent_not_the_text_none():
    h = Headless(argv=("a",), model_flag="--model", resume_flag="--resume")
    s = ld.spec_from_headless("l", h, model=None, session=None)
    assert s.argv == ("a",) and s.values == {}


def test_a_transport_that_cannot_load_is_unavailable(monkeypatch):
    monkeypatch.setitem(ld.BUILTIN_TRANSPORTS, "ghost", "ddflow.services.no_such_module")
    assert ld.driver_for("ghost").status == "unavailable"


def test_missing_executable_and_shell_are_failed_results(driver):
    assert driver.start(ld.LaunchSpec(name="x", argv=("/no/such/agent",))).status == "failed"
    r = driver.start(ld.LaunchSpec(name="x", argv=("bash", "-c", "true")))
    assert r.status == "failed" and "shell" in r.detail


def test_buffer_drops_oldest_and_counts(driver):
    h = driver.start(spec(event_buffer=2))
    deadline = time.time() + 10
    while driver.status(h).state == "running" and time.time() < deadline:
        time.sleep(0.02)
    st = driver.status(h)
    assert st.dropped == 2 and st.events == 4
    assert [e.event for e in driver.events(h)] == ["usage", "settled"]


def test_scope_systemd_without_systemd_run_is_unavailable(driver, monkeypatch):
    monkeypatch.setattr(lh.shutil, "which", lambda n: None)
    assert driver.start(spec(scope="systemd")).status == "unavailable"
    assert driver.start(spec(scope="bogus")).status == "failed"


def test_registry_and_unknown_transport():
    d = ld.driver_for("process-headless")
    assert isinstance(d, ld.Lane) and "process-headless" in ld.transports()
    r = ld.driver_for("no-such-transport")
    assert r.status == "unavailable" and "process-headless" in r.detail
    with pytest.raises(ValueError):
        ld.register_transport("process-headless", lambda: d)


def test_advertise_limits_to_driver_and_vocabulary(driver):
    assert ld.advertise(driver, {"steer", "usage", "bogus"}) == {"usage"}


def test_spec_from_headless_composes_flags_and_reports_gaps():
    h = Headless(
        argv=("agent", "run"),
        model_flag="--model",
        resume_flag="--resume",
        output_flag="--format",
        output_format="json",
        autonomy="auto",
        exit_codes={"2": "limit"},
    )
    a = Autonomy(profiles={"auto": "--yolo --no-ask"})
    s = ld.spec_from_headless("l", h, autonomy=a, model="m1", session="S1", cwd="/w")
    assert s.argv == (
        "agent",
        "run",
        "--model",
        "{model}",
        "--resume",
        "{session}",
        "--yolo",
        "--no-ask",
        "--format",
        "json",
    )
    assert s.values == {"dir": "/w", "model": "m1", "session": "S1"}
    assert s.resume and s.exit_codes == {"2": "limit"}
    # templates that already carry the placeholder are not doubled
    h2 = Headless(argv=("agent", "-m", "{model}"), model_flag="--model")
    assert ld.spec_from_headless("l", h2, model="m").argv == ("agent", "-m", "{model}")
    # gaps are unavailable, never guessed
    assert ld.spec_from_headless("l", Headless(none=True)).status == "unavailable"
    assert ld.spec_from_headless("l", Headless(argv=("a",)), effort="high").status == "unavailable"
    assert ld.spec_from_headless("l", h).status == "unavailable"  # autonomy profile absent


def test_lane_options_come_from_the_lanes_knobs():
    from ddflow.config import Config

    o = ld.lane_options(Config())
    assert o == {
        "stop_grace_s": 10.0,
        "event_buffer": 1000,
        "scope": "none",
        "pass_env": ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR"),
    }
    assert ld.LaunchSpec(name="x", argv=("a",), **o).scope == "none"


def test_a_placeholder_in_the_template_needs_no_flag():
    h = Headless(argv=("agent", "{model}"))
    s = ld.spec_from_headless("l", h, model="gpt")
    assert s.argv == ("agent", "{model}") and s.values == {"model": "gpt"}
