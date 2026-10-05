"""Host signal samplers: each degrades to None (unavailable) with a reason, never to 0.

Every platform path is driven through monkeypatched OS entry points, so these run the
same on any host and assume nothing about the machine running them.
"""

from __future__ import annotations

import builtins
import io
import os
import shutil
import subprocess
from collections import namedtuple

import pytest

from ddflow.infra import signals as S

Usage = namedtuple("Usage", "total used free")

MEMINFO = "MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    4000000 kB\n"

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                1000.
Pages active:                              5000.
Pages inactive:                            2000.
Pages speculative:                         1000.
Pages throttled:                              0.
Pages wired down:                          3000.
Pages purgeable:                            100.
"""


@pytest.fixture
def disk(monkeypatch):
    seen = []

    def fake_usage(path):
        seen.append(str(path))
        return Usage(total=1000, used=750, free=250)

    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    return seen


def _open_meminfo(monkeypatch, text):
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path) == "/proc/meminfo":
            return io.StringIO(text)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)


def _linux(monkeypatch, meminfo=MEMINFO):
    monkeypatch.setattr(S.sys, "platform", "linux")
    monkeypatch.setattr(os, "getloadavg", lambda: (2.0, 1.0, 0.5), raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    _open_meminfo(monkeypatch, meminfo)


def test_linux_meminfo_gives_available_fraction(monkeypatch, disk, tmp_path):
    _linux(monkeypatch)
    out = S.HostSignals(root=tmp_path).sample()
    assert out["memory_free_frac"] == pytest.approx(0.25)
    assert out["load"] == pytest.approx(0.25)  # 2.0 over 8 cores
    assert out["disk_free_bytes"] == 250
    assert out["disk_free_frac"] == pytest.approx(0.25)


def test_garbage_meminfo_is_none_not_an_exception(monkeypatch, disk, tmp_path):
    _linux(monkeypatch, meminfo="MemTotal: lots\nMemAvailable: some kB\n")
    src = S.HostSignals(root=tmp_path)
    out = src.sample()
    assert out["memory_free_frac"] is None
    assert "memory_free_frac" in src.reasons
    assert out["load"] is not None  # the others still sampled


def test_meminfo_without_memavailable_is_unavailable(monkeypatch, disk, tmp_path):
    _linux(monkeypatch, meminfo="MemTotal: 100 kB\nMemFree: 50 kB\n")
    src = S.HostSignals(root=tmp_path)
    assert src.sample()["memory_free_frac"] is None
    assert src.reasons["memory_free_frac"]


def test_macos_vm_stat_parsed(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "darwin")
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 1.0, 1.0), raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("timeout")))
        out = VM_STAT if cmd[0] == "vm_stat" else str(16000 * 16384) + "\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = S.HostSignals(root=tmp_path).sample()
    # free + inactive + speculative = 4000 pages of 16000 (purgeable overlaps them)
    assert out["memory_free_frac"] == pytest.approx(0.25)
    assert out["load"] == pytest.approx(0.25)
    assert all(t == pytest.approx(2.0) for _, t in calls)
    assert out["disk_free_bytes"] == 250


def test_macos_timeout_is_none_with_reason(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "darwin")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(subprocess, "run", slow)
    src = S.HostSignals(root=tmp_path)
    assert src.sample()["memory_free_frac"] is None
    assert "timed out" in src.reasons["memory_free_frac"]


def test_windows_load_none_others_sampled(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "win32")

    def no_loadavg():
        raise AttributeError("module 'os' has no attribute 'getloadavg'")

    monkeypatch.setattr(os, "getloadavg", no_loadavg, raising=False)
    monkeypatch.setattr(S, "_windows_memory", lambda: (3.0, 12.0))
    src = S.HostSignals(root=tmp_path)
    out = src.sample()
    assert out["load"] is None
    assert "load" in src.reasons
    assert out["memory_free_frac"] == pytest.approx(0.25)
    assert out["disk_free_bytes"] == 250


def test_windows_without_getloadavg_attribute(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "win32")
    monkeypatch.delattr(os, "getloadavg", raising=False)
    monkeypatch.setattr(S, "_windows_memory", lambda: (1.0, 4.0))
    out = S.HostSignals(root=tmp_path).sample()
    assert out["load"] is None
    assert out["memory_free_frac"] == pytest.approx(0.25)


def test_other_platform_memory_unavailable(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "freebsd14")
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 1.0, 1.0), raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    src = S.HostSignals(root=tmp_path)
    out = src.sample()
    assert out["memory_free_frac"] is None
    assert out["load"] == pytest.approx(0.5)
    assert out["disk_free_bytes"] == 250


@pytest.mark.parametrize("plat", ["linux", "darwin", "win32", "freebsd14"])
def test_disk_sampled_on_every_platform(monkeypatch, disk, tmp_path, plat):
    monkeypatch.setattr(S.sys, "platform", plat)
    monkeypatch.setattr(S, "_memory_free_frac", lambda timeout: None)
    out = S.HostSignals(root=tmp_path).sample()
    assert out["disk_free_bytes"] == 250
    assert out["disk_free_frac"] == pytest.approx(0.25)
    assert disk == [str(tmp_path)]


def test_disk_falls_back_to_repo_root(monkeypatch, disk, tmp_path):
    missing = tmp_path / "gone"
    out = S.HostSignals(root=missing, fallback_root=tmp_path).sample()
    assert out["disk_free_bytes"] == 250
    assert disk == [str(tmp_path)]


def test_a_sampler_that_raises_is_none_plus_reason(monkeypatch, tmp_path):
    def boom(path):
        raise OSError("device not ready")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    src = S.HostSignals(root=tmp_path)
    out = src.sample()
    assert out["disk_free_bytes"] is None and out["disk_free_frac"] is None
    assert "device not ready" in src.reasons["disk_free_bytes"]
    assert "\n" not in src.reasons["disk_free_bytes"]


def test_zero_cpu_count_is_unavailable_not_a_crash(monkeypatch, disk, tmp_path):
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 1.0, 1.0), raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    src = S.HostSignals(root=tmp_path)
    assert src.sample()["load"] is None
    assert src.reasons["load"]


def test_reasons_clear_once_a_signal_recovers(monkeypatch, disk, tmp_path):
    _linux(monkeypatch, meminfo="junk")
    src = S.HostSignals(root=tmp_path)
    src.sample()
    assert "memory_free_frac" in src.reasons
    _open_meminfo(monkeypatch, MEMINFO)
    src.sample()
    assert "memory_free_frac" not in src.reasons


def test_every_signal_is_always_present(monkeypatch, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "plan9")
    monkeypatch.delattr(os, "getloadavg", raising=False)

    def boom(path):
        raise OSError("no")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    src = S.HostSignals(root=tmp_path)
    out = src.sample()
    assert set(out) == set(S.SIGNALS)
    assert all(v is None for v in out.values())
    assert set(src.reasons) == set(S.SIGNALS)


def test_fake_source_scripts_values():
    fake = S.FakeSource([{"load": 0.1}, {"load": 0.9, "memory_free_frac": None}])
    assert isinstance(fake, S.SignalSource)
    assert fake.sample()["load"] == 0.1
    second = fake.sample()
    assert second["load"] == 0.9 and second["memory_free_frac"] is None
    assert fake.sample()["load"] == 0.9  # the last script repeats
    assert set(fake.sample()) == set(S.SIGNALS)


def test_host_signals_is_a_signal_source(tmp_path):
    assert isinstance(S.HostSignals(root=tmp_path), S.SignalSource)


def test_doctor_note_names_unavailable_signals():
    fake = S.FakeSource([{"load": None, "memory_free_frac": 0.5}])
    fake.reasons = {"load": "os.getloadavg is not available on this platform"}
    notes = S.doctor_notes(fake)
    assert len(notes) == 1
    assert "load" in notes[0] and "getloadavg" in notes[0]
    assert "start value" in notes[0]
    assert (
        S.doctor_notes(
            S.FakeSource(
                [
                    {
                        "load": 0.1,
                        "memory_free_frac": 0.5,
                        "disk_free_bytes": 1.0,
                        "disk_free_frac": 0.5,
                    }
                ]
            )
        )
        == []
    )


def test_doctor_reports_unavailable_host_signal_as_a_note(repo, monkeypatch):
    import sys as _sys
    from pathlib import Path

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import run_cli

    from ddflow.api import reporting

    assert run_cli(repo, "init")[0] == 0
    monkeypatch.delattr(os, "getloadavg", raising=False)
    out = reporting.doctor(repo, agent="a1")
    notes = [n for n in out.data["notes"] if n.startswith("host signals unavailable")]
    assert len(notes) == 1 and "load (" in notes[0], out.data["notes"]
    assert not any("host signal" in p for p in out.data["problems"])
