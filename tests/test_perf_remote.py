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


def test_the_one_json_line_round_trips_as_agent_reported():
    s = CommandSample(1.5, 0.9, 50.0, None, "psutil", TRUST_MEASURED, container=False)
    back = parse_line(s.one_line())
    assert back.wall_s == 1.5 and back.cpu_s == 0.9
    # an ingested line is always agent-reported: a posted line cannot prove it ran the
    # wrapper, so its self-declared `measured` is never taken at face value
    assert back.trust == TRUST_AGENT


def test_parse_line_coerces_the_reported_numbers_to_floats():
    back = parse_line('{"wall_s": "1.5", "cpu_s": "2.5", "rss_mb": "10", "io_rw": "3"}')
    # numeric strings become floats, so budgets and rollups never mix types
    assert back.cpu_s == 2.5 and back.rss_mb == 10.0 and back.io_rw == 3.0
    # a non-numeric field is dropped, never kept as a string
    assert parse_line('{"wall_s": "1.5", "cpu_s": "two"}').cpu_s is None


def test_a_posted_env_key_that_is_not_a_digest_is_dropped():
    # a hostname or username is not a machine-class digest: dropped, never mixed into a key
    assert parse_line('{"wall_s": 1, "env": "webserver01"}').env == ""
    assert parse_line('{"wall_s": 1, "env": "abcdef012345"}').env == "abcdef012345"


def test_an_agent_line_cannot_be_promoted_to_measured():
    forged = CommandSample(1.0, None, None, None, "wait4", TRUST_MEASURED, container=False)
    # the self-declared trust field is ignored -- only measure() produces `measured`
    assert parse_line(forged.one_line()).trust == TRUST_AGENT


def test_wrap_command_names_the_exact_command():
    # shell-quoted, so --cmd binds the whole command as one token
    assert wrap_command("pytest -q", program="ddflow") == "ddflow perf run --cmd 'pytest -q'"


def test_fingerprint_container_never_mixes_container_with_bare_metal():
    fp = Fingerprint(key="abcdef012345", cond="idle")
    assert fingerprint_container(fp, in_ctr=True, vm=False) == "abcdef012345.container"
    assert fingerprint_container(fp, in_ctr=False, vm=False) == "abcdef012345.bare"
    assert fingerprint_container(fp, in_ctr=False, vm=True) == "abcdef012345.vm"
    assert fingerprint_container(fp, in_ctr=None, vm=None) == "abcdef012345.unavailable"


def test_the_default_sample_thread_gives_measured_trust():
    s = CommandSample(0.5, None, None, None, "wait4")
    assert s.trust == TRUST_MEASURED
    assert SOURCE_LADDER == ("cgroup", "psutil", "proc", "wait4")
