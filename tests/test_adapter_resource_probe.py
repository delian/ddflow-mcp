"""The live resource sampler port + fake (PM-sampler): degrade down the ladder, unavailable."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.adapters.fakes.resource_probe import FakeProbe
from ddflow.adapters.resource_probe import NETWORK_NOT_COLLECTED, degrade, sample


def test_the_fake_returns_its_canned_sample_and_degrade_uses_it():
    got = degrade([FakeProbe(rss_kb=2048, source="proc")], pid=1)
    assert got.rss_kb == 2048 and got.source == "proc"


def test_degrade_falls_through_to_the_next_adapter_when_one_is_unavailable():
    # the first returns None (the whole ladder-up adapter said unavailable), the second wins
    got = degrade([FakeProbe(rss_kb=None), FakeProbe(rss_kb=4096, source="ps")], pid=1)
    assert got.rss_kb == 4096 and got.source == "ps"


def test_when_the_whole_ladder_is_unavailable_the_sample_is_unavailable():
    got = degrade([FakeProbe(rss_kb=None)], pid=1)
    assert got.rss_kb is None and got.source == "unavailable"


def test_sample_defaults_to_the_stdlib_ladder_and_a_fake_short_circuits_it():
    # A real /proc probe never runs in tests: pass the fake, and sample() uses it.
    got = sample(1, [FakeProbe(rss_kb=777)])
    assert got.rss_kb == 777


def test_no_sampler_returns_a_network_field_and_unavailable_is_the_marker():
    from ddflow.adapters.resource_probe import ResourceSample

    # the ResourceSample has no network field; the not-collected contract is the marker above
    assert not any("net" in f for f in ResourceSample.__dataclass_fields__)
    assert "not collected" in NETWORK_NOT_COLLECTED
