"""remote: the measuring wrapper, agent-reported trust and the container fingerprint.

The falsifier is a wrapper that subtracts two hosts' timestamps, treats an agent-reported
timing as gate evidence, or mixes a container series with a bare-metal one; these tests run
the wrapper through a fake runner/probe, round-trip the one JSON line, and keep the two
trust levels apart.
"""

from __future__ import annotations

from ddflow.services.perf.env import Fingerprint
from ddflow.services.perf.remote import (
    SOURCE_LADDER,
    TRUST_AGENT,
    TRUST_MEASURED,
    CommandSample,
    fingerprint_container,
    measure,
    parse_line,
    wrap_command,
)


def _probe(numbers: dict[str, dict[str, float | None]]):
    return numbers.get


def test_measure_degrades_the_ladder_and_records_the_source():
    calls: list[float] = []

    def clock() -> float:
        calls.append(1)
        return float(len(calls) * 1.0)

    sample = measure(
        lambda: None,
        probe=_probe({"psutil": {"cpu_s": 1.2, "rss_mb": 40.0, "io_rw": None}}),
        clock=clock,
    )
    assert sample.wall_s > 0
    assert sample.cpu_s == 1.2 and sample.rss_mb == 40.0
    assert sample.source == "psutil"  # the first ladder rung that reported a number


def test_measure_names_unavailable_when_no_rung_reports():
    sample = measure(lambda: None, probe=_probe({}))
    assert sample.source == "unavailable"
    assert sample.cpu_s is None  # never zero


def test_the_one_json_line_round_trips():
    s = CommandSample(1.5, 0.9, 50.0, None, "psutil", TRUST_MEASURED, container=False)
    back = parse_line(s.one_line())
    assert back.wall_s == 1.5 and back.cpu_s == 0.9
    # the wrapper's own line is accepted as measured (never demoted)
    assert back.trust == TRUST_MEASURED


def test_an_agent_line_cannot_be_promoted_to_measured():
    forged = CommandSample(1.0, None, None, None, "wait4", TRUST_MEASURED, container=False)
    # a line without the wrapper's own `trust` field is agent-reported, never measured
    assert (
        parse_line(
            forged.one_line().replace('"trust": "measured"', '"trust": "agent-reported"')
        ).trust
        == TRUST_AGENT
    )


def test_wrap_command_names_the_exact_command():
    assert wrap_command("pytest -q", program="ddflow") == "ddflow perf run -- pytest -q"


def test_fingerprint_container_never_mixes_container_with_bare_metal():
    fp = Fingerprint(key="abcdef012345", cond="idle")
    assert fingerprint_container(fp, in_ctr=True).endswith("container=container")
    assert fingerprint_container(fp, in_ctr=False).endswith("container=bare")
    assert fingerprint_container(fp, in_ctr=None).endswith("container=unavailable")


def test_the_default_sample_thread_gives_measured_trust():
    s = CommandSample(0.5, None, None, None, "wait4")
    assert s.trust == TRUST_MEASURED
    assert SOURCE_LADDER == ("cgroup", "psutil", "proc", "wait4")
