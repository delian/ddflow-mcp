"""The lane driver's process knobs stay declared in the upgrade manifest

(B6fae86acc5): `lanes.stop_grace_s`, `lanes.event_buffer`, `lanes.scope` and
`lanes.pass_env` shipped in the lane contract and process-headless driver commits but
were never added to the upgrade manifest, so `UM.replay()` fell behind the live code and
`test_the_current_code_has_exactly_the_knobs_and_event_kinds_the_manifest_gives_it` went
red on main. Each is declared as one unreleased fragment; this test locks the declaration
so the drift cannot come back silently.
"""

from __future__ import annotations

from ddflow.services import upgrade_manifest as UM


def test_the_lane_process_knobs_are_declared_in_the_upgrade_manifest() -> None:
    knobs, _ = UM.replay()
    assert knobs["lanes.event_buffer"] == 1000
    assert knobs["lanes.pass_env"] == ["PATH", "LANG", "LC_ALL", "TERM", "TMPDIR"]
    assert knobs["lanes.scope"] == "none"
    assert knobs["lanes.stop_grace_s"] == 10
