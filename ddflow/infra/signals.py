"""Host signals for adaptive parallelism: load per core, available memory, free disk.

Each sampler is portable and degrades to ``None`` -- UNAVAILABLE -- on a platform that
cannot supply it, never to ``0``: a zero would read as "perfectly healthy" (or, for free
memory, as "exhausted") and move the limit on no evidence, while ``None`` is neutral to
the controller (``core/flowcontrol``). Every sampler is wrapped, so an exception or a
timeout yields ``None`` plus a one-line reason kept in :attr:`HostSignals.reasons` for
``ddflow doctor`` and the parallelism report.

=================== ============================================= =====================
signal              how                                           unavailable when
=================== ============================================= =====================
``load``            ``os.getloadavg()[0] / os.cpu_count()``       no getloadavg (Windows)
``memory_free_frac`` Linux: MemAvailable/MemTotal, /proc/meminfo;  any other platform, a
                    macOS: vm_stat + ``sysctl hw.memsize`` (2 s);  parse error, a timeout
                    Windows: GlobalMemoryStatusEx
``disk_free_bytes`` ``shutil.disk_usage`` of the worktree root,   the call fails
``disk_free_frac``  falling back to the repo root (every platform)
=================== ============================================= =====================

Stdlib only. Nothing here reads the event log or the config: the caller decides where
the roots are and how often to sample.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

#: Every name a :class:`SignalSource` returns, always all of them (``None`` = unavailable).
SIGNALS = ("load", "memory_free_frac", "disk_free_bytes", "disk_free_frac")

#: Seconds a macOS helper process may take before the memory signal is unavailable.
SUBPROCESS_TIMEOUT_S = 2.0


class Unavailable(Exception):
    """A sampler cannot supply its signal here; the message is the reason."""


@runtime_checkable
class SignalSource(Protocol):
    """Anything that samples the host. ``reasons`` names each unavailable signal's cause."""

    reasons: dict[str, str]

    def sample(self) -> dict[str, float | None]: ...


def _one_line(text: str, limit: int = 160) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _load() -> float:
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        raise Unavailable("os.getloadavg is not available on this platform")
    try:
        one_minute = getloadavg()[0]
    except (AttributeError, OSError) as exc:
        raise Unavailable(f"os.getloadavg failed: {exc}") from exc
    cores = os.cpu_count()
    if not cores:
        raise Unavailable("os.cpu_count() reports no core count")
    return float(one_minute) / cores


def _meminfo_kib(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        m = re.fullmatch(r"\s*(\d+)(?:\s*kB)?\s*", rest)
        if m:
            out[name.strip()] = int(m.group(1))
    return out


def _linux_memory() -> float:
    with open("/proc/meminfo", encoding="ascii", errors="replace") as fh:
        info = _meminfo_kib(fh.read())
    if "MemAvailable" not in info or not info.get("MemTotal"):
        raise Unavailable("/proc/meminfo has no MemAvailable/MemTotal")
    return info["MemAvailable"] / info["MemTotal"]


def _run(cmd: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Unavailable(f"{cmd[0]} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise Unavailable(f"{cmd[0]} could not run: {exc}") from exc
    if done.returncode != 0:
        raise Unavailable(f"{cmd[0]} exited {done.returncode}")
    return done.stdout or ""


def _macos_memory(timeout: float) -> float:
    vm = _run(["vm_stat"], timeout)
    page = re.search(r"page size of (\d+) bytes", vm)
    pages = {
        m.group(1).strip().lower(): int(m.group(2))
        for m in re.finditer(r'^"?([^:"]+)"?:\s+(\d+)\.?\s*$', vm, re.MULTILINE)
    }
    total = int(_run(["sysctl", "-n", "hw.memsize"], timeout).strip())
    wanted = ("pages free", "pages inactive", "pages speculative")
    if not page or total <= 0 or any(k not in pages for k in wanted):
        raise Unavailable("vm_stat output not understood")
    return sum(pages[k] for k in wanted) * int(page.group(1)) / total


def _windows_memory() -> tuple[float, float]:
    """(available, total) physical bytes from GlobalMemoryStatusEx."""
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MemoryStatusEx()
    stat.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
        raise Unavailable("GlobalMemoryStatusEx failed")
    return float(stat.ullAvailPhys), float(stat.ullTotalPhys)


def _memory_free_frac(timeout: float) -> float | None:
    plat = sys.platform
    if plat.startswith("linux"):
        return _linux_memory()
    if plat == "darwin":
        return _macos_memory(timeout)
    if plat == "win32":
        avail, total = _windows_memory()
        if total <= 0:
            raise Unavailable("GlobalMemoryStatusEx reports no physical memory")
        return avail / total
    raise Unavailable(f"no memory sampler for platform {plat!r}")


def _disk(roots: Sequence[Path | None]) -> tuple[float, float]:
    candidates = [Path(r) for r in roots if r is not None]
    if not candidates:
        raise Unavailable("no directory to measure")
    target = next((p for p in candidates if p.exists()), candidates[-1])
    usage = shutil.disk_usage(str(target))
    if usage.total <= 0:
        raise Unavailable(f"disk_usage({target}) reports a total of 0")
    return float(usage.free), usage.free / usage.total


class HostSignals:
    """Samples this host. ``root`` is the worktree to measure the disk of; when it does
    not exist ``fallback_root`` (the repository root) is measured instead."""

    def __init__(
        self,
        root: Path | str | None = None,
        fallback_root: Path | str | None = None,
        *,
        timeout_s: float = SUBPROCESS_TIMEOUT_S,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.fallback_root = Path(fallback_root) if fallback_root is not None else None
        if self.root is None and self.fallback_root is None:
            self.fallback_root = Path.cwd()
        self.timeout_s = timeout_s
        self.reasons: dict[str, str] = {}

    def _guard(self, names: tuple[str, ...], fn: Callable[[], object]) -> object | None:
        try:
            value = fn()
        except Exception as exc:  # any failure is "unavailable", by contract
            why = str(exc) if isinstance(exc, Unavailable) else f"{type(exc).__name__}: {exc}"
            for n in names:
                self.reasons[n] = _one_line(why or type(exc).__name__)
            return None
        for n in names:
            self.reasons.pop(n, None)
        return value

    def sample(self) -> dict[str, float | None]:
        out: dict[str, float | None] = dict.fromkeys(SIGNALS)
        out["load"] = self._guard(("load",), _load)  # type: ignore[assignment]
        out["memory_free_frac"] = self._guard(  # type: ignore[assignment]
            ("memory_free_frac",), lambda: _memory_free_frac(self.timeout_s)
        )
        disk = self._guard(
            ("disk_free_bytes", "disk_free_frac"),
            lambda: _disk([self.root, self.fallback_root]),
        )
        if disk is not None:
            out["disk_free_bytes"], out["disk_free_frac"] = disk  # type: ignore[misc]
        return out


class FakeSource:
    """Scripted signals for tests: each :meth:`sample` returns the next mapping (missing
    names are ``None``); the last one repeats once the script runs out."""

    def __init__(self, script: Sequence[Mapping[str, float | None]] | Mapping[str, float | None]):
        self.script = [script] if isinstance(script, Mapping) else list(script)
        self.calls = 0
        self.reasons: dict[str, str] = {}

    def sample(self) -> dict[str, float | None]:
        if not self.script:
            values: Mapping[str, float | None] = {}
        else:
            values = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        out: dict[str, float | None] = dict.fromkeys(SIGNALS)
        out.update(values)
        return out


def doctor_notes(source: SignalSource | None = None) -> list[str]:
    """One NOTE (never a problem) naming the host signals unavailable here and why."""
    src = source if source is not None else HostSignals()
    values = src.sample()
    missing = [n for n in SIGNALS if values.get(n) is None]
    if not missing:
        return []
    detail = "; ".join(f"{n} ({src.reasons.get(n, 'no value')})" for n in missing)
    return [
        f"host signals unavailable on this platform: {detail} -- adaptive parallelism "
        "steers by the signals that remain, and holds at its start value when no host "
        "signal is available"
    ]
