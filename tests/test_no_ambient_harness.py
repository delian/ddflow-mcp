"""The suite answers the same under an agent harness as on CI (B-tests-inherit-claudecode).

A harness marker says "this call is an agent's", so inherited it refuses every person-only path
a test takes, and the failure count differs by who ran the suite."""

from __future__ import annotations

import os

from ddflow.services import identity as ID


def test_no_harness_marker_reaches_a_test():
    assert not [var for var in ID.HARNESS_MARKERS if var in os.environ]
    assert ID.agent_marker() == "" or "DDFLOW_AGENT" in ID.agent_marker()
