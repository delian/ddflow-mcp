"""PM-resource-capture: per-command cpu, peak rss and block io from the kernel's rusage."""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ddflow.infra import proc as P
from ddflow.adapters import resources as R

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


def _self_peak_kb() -> int:
    import resource  # POSIX only: the callers are posix_only

    return R.maxrss_kb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _touch(mb: int) -> bytearray:
    buf = bytearray(mb * 1024 * 1024)
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    return buf


def _burn_beyond_this_process(extra_mb: int) -> str:
    """A child line that touches this process's own peak plus ``extra_mb``."""
    mb = _self_peak_kb() // 1024 + extra_mb
    return (
        f"{_PY} -c 'b = bytearray({mb} * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1'"
    )


def _assert_each_command_has_its_own_peak() -> None:
    # A forked child shares its parent's address space until exec, and the kernel folds the
    # parent's high-water mark into the child's ru_maxrss: a cheap command in a big process
    # reports that process's peak (B0cdef4bf3a). So no absolute size and no "cheap < heavy"
    # comparison is stable; the stable facts are relative to this process's own peak.
    before = _self_peak_kb()
    heavy = P.run_shell(_burn_beyond_this_process(200), timeout_s=120).resources
    again = P.run_shell("true", timeout_s=20).resources
    after = _self_peak_kb()
    assert heavy.source == again.source == "wait4"
    # More than anything inheritance could explain: the child's own allocation.
    assert heavy.maxrss_kb >= before + 150 * 1024
    # A running total would carry the heavy child's mark into `again`, far above this
    # process's own peak; an inherited mark never exceeds it (B37281998f7).
    assert again.maxrss_kb <= after + 64 * 1024
    assert again.maxrss_kb < heavy.maxrss_kb


@posix_only
def test_each_command_gets_its_own_numbers_not_a_running_total():
    _assert_each_command_has_its_own_peak()


@posix_only
def test_a_cheap_command_in_a_big_process_does_not_fail_the_per_command_check():
    """B0cdef4bf3a: with a 400 MB parent, `true` reports ~400 MB (inherited), which broke the
    fixed-size comparison the test used; the relative one holds in a process of any size."""
    ballast = _touch(400)
    cheap = P.run_shell("true", timeout_s=20).resources
    assert cheap.maxrss_kb >= 300 * 1024, "premise: the kernel reports the parent's peak"
    _assert_each_command_has_its_own_peak()
    assert len(ballast) > 0


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
