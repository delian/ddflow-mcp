"""Run one declared check against its fixture: E4 argv, E5 stream, and the Lane contract."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from ddflow.services import harnessreg as HR
from ddflow.services import lane_driver as ld
from ddflow.services.engines import render_argv, stream_map

FAKE = Path(__file__).with_name("fake_agent.py")


def check_argv(hid: str, fx: dict) -> None:
    h = HR.get(hid)
    spec = ld.spec_from_headless(hid, h.headless, autonomy=h.autonomy, **fx["inputs"])
    assert not isinstance(spec, ld.Result), (hid, spec)
    got = render_argv.render(spec.argv, spec.values)
    assert not got.missing
    assert list(got.argv) == fx["expect"], hid


def _events(fx: dict) -> list[list]:
    rules = stream_map.parse_rules(fx["rules"])
    import json

    lines = [json.dumps(x) if not isinstance(x, str) else x for x in fx["lines"]]
    stream = stream_map.map_stream(lines, rules)
    out = [[e.event, e.fields] for e in stream]
    assert stream.skipped == fx["expect"]["skipped"]
    return out


def check_stream(hid: str, fx: dict, fixture_path: Path) -> None:
    """E5 over the lines, then the Lane contract: the driver run against the fake agent."""
    assert _events(fx) == fx["expect"]["events"], hid
    h = HR.get(hid)
    spec = ld.spec_from_headless(hid, h.headless, autonomy=h.autonomy, stop_grace_s=2.0)
    assert not isinstance(spec, ld.Result)
    # the descriptor's own launch is rendered above; here the program is the fake agent
    run_spec = ld.LaunchSpec(
        name=f"conf-{hid}",
        argv=(sys.executable, str(FAKE), str(fixture_path)),
        stream_rules=stream_map.parse_rules(fx["rules"]),
        exit_codes=spec.exit_codes,
        stop_grace_s=2.0,
    )
    driver = ld.driver_for("process-headless")
    assert not isinstance(driver, ld.Result), driver
    assert isinstance(driver, ld.Lane)
    handle = driver.start(run_spec)
    assert not isinstance(handle, ld.Result), handle
    try:
        got = [[e.event, dict(e.fields)] for e in driver.events(handle)]
        deadline = time.monotonic() + 10
        while driver.status(handle).state == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        st = driver.status(handle)
        assert got == fx["expect"]["events"], hid
        assert st.state == fx["expect"]["state"], (hid, st)
        assert st.skipped == fx["expect"]["skipped"]
        assert st.capabilities <= ld.CAPABILITIES
        assert handle.capabilities <= driver.capabilities
        if "steer" not in handle.capabilities:  # never imitated
            assert driver.steer(handle, "hi").status == "unavailable"
    finally:
        assert driver.stop(handle).status == "ok"
