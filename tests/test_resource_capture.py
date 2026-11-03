"""PM-resource-capture: per-command cpu, peak rss and block io from the kernel's rusage."""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ddflow.infra import proc as P
from ddflow.infra import resources as R

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="rusage is POSIX only")

_PY = shlex.quote(sys.executable)
_BURN = (
    "import time\n"
    "buf = bytearray(150 * 1024 * 1024)\n"
    "for i in range(0, len(buf), 4096): buf[i] = 1\n"
    "end = time.process_time() + 0.4\n"
    "while time.process_time() < end: pass\n"
)


def _ru(**kw):
    base = {
        "ru_utime": 1.2345,
        "ru_stime": 0.5,
        "ru_maxrss": 2048,
        "ru_inblock": 3,
        "ru_oublock": 4,
    }
    return SimpleNamespace(**{**base, **kw})


def test_maxrss_is_normalised_to_kb_per_platform():
    assert R.maxrss_kb(2048, "linux") == 2048
    assert R.maxrss_kb(2048 * 1024, "darwin") == 2048


def test_macos_block_io_is_unknown_not_zero():
    got = R.from_rusage(_ru(ru_inblock=0, ru_oublock=0), platform="darwin")
    assert (got.io_in_blocks, got.io_out_blocks) == (None, None)


def test_from_rusage_fake_gives_the_evidence_fields():
    ev = R.from_rusage(_ru(), platform="linux").evidence()
    assert ev == {
        "cpu_user_s": round(1.2345, 3),
        "cpu_sys_s": 0.5,
        "maxrss_kb": 2048,
        "io_in_blocks": 3,
        "io_out_blocks": 4,
        "resources_source": "wait4",
    }


def test_measure_prefers_wait4_then_delta_then_unavailable(monkeypatch):
    assert R.measure(SimpleNamespace(rusage=_ru()), None).source == "wait4"
    before, after = _ru(), _ru(ru_utime=2.2345, ru_inblock=10, ru_maxrss=4096)
    monkeypatch.setattr(R, "children_snapshot", lambda: after)
    d = R.measure(SimpleNamespace(rusage=None), before)
    # peak rss is a high-water mark, not a difference: unknown (None) in a delta, never a guess
    assert (d.source, d.cpu_user_s, d.io_in_blocks, d.maxrss_kb) == ("delta", 1.0, 7, None)
    monkeypatch.setattr(R, "children_snapshot", lambda: None)
    u = R.measure(SimpleNamespace(rusage=None), None)
    assert u.source == "unavailable"
    assert all(v is None for k, v in u.evidence().items() if k != "resources_source")


@posix_only
def test_real_child_that_allocates_and_burns_cpu_is_measured():
    r = P.run_shell(f"{_PY} -c '{_BURN}'", timeout_s=60)
    assert r.ok, r.err
    res = r.resources
    assert res is not None and res.source == "wait4"
    assert res.maxrss_kb >= 130 * 1024
    assert res.cpu_user_s + res.cpu_sys_s >= 0.35


@posix_only
def test_each_command_gets_its_own_numbers_not_a_running_total():
    cheap = P.run_shell("true", timeout_s=20).resources
    heavy = P.run_shell(f"{_PY} -c '{_BURN}'", timeout_s=60).resources
    assert cheap.source == "wait4"
    assert heavy.maxrss_kb >= 130 * 1024
    # `again` must report its OWN numbers, not the earlier child's high-water mark (peak
    # rss is a high-water mark). The `wait4` cpu sample is load-sensitive: at 48 workers a
    # not-yet-reaped sibling can be attributed to this line once, so one ratio sample is
    # flaky under full-suite load (B37281998f7); retry before failing.
    for _ in range(3):
        again = P.run_shell("true", timeout_s=20).resources
        if (
            again.source == "wait4"
            and again.maxrss_kb < heavy.maxrss_kb
            and again.cpu_user_s + again.cpu_sys_s < (heavy.cpu_user_s + heavy.cpu_sys_s) / 2
        ):
            return
    pytest.fail("again carried the earlier child's high-water cpu or rss mark under load")


@posix_only
def test_a_process_tree_is_attributed_to_the_shell_line():
    line = f"{_PY} -c '{_BURN}' & wait"
    res = P.run_shell(line, timeout_s=60).resources
    assert res.cpu_user_s + res.cpu_sys_s >= 0.35


@posix_only
def test_a_timed_out_command_still_reports_what_it_cost():
    r = P.run_shell(f"{_PY} -c 'while True: pass'", timeout_s=0.8)
    assert r.timed_out and r.resources is not None
    # A killed tree's unreaped descendants are not the shell's to report: only that numbers
    # exist (never a fabricated zero for "unavailable") is promised.
    assert r.resources.source == "wait4" and r.resources.maxrss_kb > 0


def test_a_command_that_never_started_has_no_resources(tmp_path):
    r = P.run_shell("true", timeout_s=5, cwd=tmp_path / "missing")
    assert r.could_not_run and r.resources is None


@posix_only
def test_a_timed_out_capture_still_reports_a_delta():
    c = P.capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3)
    assert c.timed_out and c.resources is not None and c.resources.source == "delta"


@posix_only
def test_capture_reports_an_approximate_delta():
    c = P.capture([sys.executable, "-c", "print(1)"], timeout=20)
    assert c.resources is not None and c.resources.source == "delta"


@posix_only
def test_wait4_unusable_falls_back_to_delta_marked_approximate(monkeypatch):
    """A reap that yields no rusage (SIGCHLD ignored) is a delta, not a wait4."""
    monkeypatch.setattr(P.MeteredPopen, "_try_wait", subprocess.Popen._try_wait)
    r = P.run_shell("true", timeout_s=20)
    assert r.resources.source == "delta"


@posix_only
def test_gate_evidence_carries_the_fields_through_cmdrunner(tmp_path):
    from ddflow.services import cmdrunner
    from ddflow.services.gates.runner import _cost_evidence

    run = cmdrunner.CommandRunner().run(
        cmdrunner.Declared("true", "test"), cwd=tmp_path, timeout_s=20
    )
    ev = _cost_evidence(run)
    assert set(ev) == {
        "cpu_user_s",
        "cpu_sys_s",
        "maxrss_kb",
        "io_in_blocks",
        "io_out_blocks",
        "resources_source",
    }
    assert ev["resources_source"] == "wait4"
    assert _cost_evidence(SimpleNamespace(resources=None)) == {}


@posix_only
def test_overhead_per_command_is_tiny():
    """The metered reap costs microseconds: compare the same loop with a plain Popen reap."""
    n = 40

    def timed(cls):
        t = time.perf_counter()
        for _ in range(n):
            cls(["true"], stdin=subprocess.DEVNULL).wait()
        return (time.perf_counter() - t) / n

    timed(subprocess.Popen)  # warm
    plain = min(timed(subprocess.Popen) for _ in range(3))
    metered = min(timed(P.MeteredPopen) for _ in range(3))
    assert metered - plain < 0.005, (plain, metered)
