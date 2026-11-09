"""The optional LIVE resource sampler, behind a port (PM-sampler).

Only RSS is sampled (full memory info is slow), on a cadence the caller chooses (default 1 s,
only on gates longer than a [perf_metrics] knob, never on short ones). The port degrades down
the ladder ``cgroup -> psutil -> /proc -> ps``; each number records WHICH source gave it and is
None (never 0) when the whole ladder is unavailable on this host. Network bytes are not
collected (no portable stdlib way): a per-run host-wide counter delta is a labelled
approximation, and the answer says not-collected, never zero.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

#: The default stdlib ladder is proc then ps; a host prepends its OPTIONAL cgroup/psutil
#: adapters in the cgroup -> psutil -> proc -> ps degrade order and passes the list to ``sample``.
UNAVAILABLE = "unavailable"
#: after the comm (state + its preceding space) the stat rest starts at field 3; rss (field 24)
#: is index 21 of the rest. 22 ensures index 21 exists.
_STAT_REST_MIN = 22


@dataclass(frozen=True)
class ResourceSample:
    """One live sample. ``rss_kb`` is None (never 0) when the ladder could not run; ``source``
    names which adapter produced it (``unavailable`` when none could)."""

    rss_kb: int | None
    source: str
    io_in_blocks: int | None = None
    io_out_blocks: int | None = None


class ResourceProbe(ABC):
    """The port: one live RSS sample of a process. Optional adapters (cgroup v2, psutil)
    implement this; the stdlib /proc and ps adapters live in this module."""

    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def sample(self, pid: int) -> ResourceSample | None:
        """RSS KB of ``pid`` (and its tree, when the adapter counts it), else None."""


def _proc_rss_kb(pid: int) -> int | None:
    """``/proc/<pid>/stat`` field 24 (RSS in pages), the stdlib Linux adapter. The stat line's
    ``comm`` field may contain spaces and parens, so parse from the last ``)``."""
    try:
        line = Path(f"/proc/{pid}/stat").read_text("utf-8", errors="replace")
    except OSError:
        return None
    rest = line.rsplit(")", 1)[-1].split()
    if len(rest) < _STAT_REST_MIN:
        return None
    try:
        pages = int(rest[21])  # rss is the 24th field: 2 before the split remainder index 21
    except (ValueError, IndexError):
        return None
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    return pages * page_kb


class ProcProbe(ResourceProbe):
    """The stdlib Linux adapter: RSS via ``/proc/<pid>/stat`` (never the command's args)."""

    def name(self) -> str:
        return "proc"

    def sample(self, pid: int) -> ResourceSample | None:
        rss = _proc_rss_kb(pid)
        return ResourceSample(rss, "proc") if rss is not None else None


class PsProbe(ResourceProbe):
    """The stdlib ``ps`` adapter for hosts without ``/proc`` (macOS). Returns RSS in KB."""

    def name(self) -> str:
        return "ps"

    def sample(self, pid: int) -> ResourceSample | None:
        import subprocess

        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5
            )
            rss_kb = int(out.stdout.strip().split()[0])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return None
        return ResourceSample(rss_kb, "ps")


def degrade(probes: list[ResourceProbe], pid: int) -> ResourceSample:
    """Try the port's adapters in the ladder order; the first that returns a sample wins. If
    the WHOLE ladder is unavailable the sample is ``unavailable`` (never a 0)."""
    for probe in probes:
        got = probe.sample(pid)
        if got is not None:
            return got
    return ResourceSample(None, UNAVAILABLE)


def stdlib_ladder() -> list[ResourceProbe]:
    """The default stdlib-only ladder (proc then ps); a host adds the optional cgroup and
    psutil adapters/implementations in front (cgroup -> psutil -> proc -> ps) and passes the list to
    ``sample`` so the degrade preserves the cgroup -> psutil -> proc -> ps contract."""
    return [ProcProbe(), PsProbe()]


def sample(pid: int, probes: list[ResourceProbe] | None = None) -> ResourceSample:
    """One live sample of ``pid``, defaulting to the stdlib /proc then ps ladder. An explicit
    empty list means every adapter declined: the sample is ``unavailable``, never a live read."""
    probes = stdlib_ladder() if probes is None else probes
    return degrade(probes, pid)


NETWORK_NOT_COLLECTED = (
    "network bytes per command: not collected (no portable stdlib way; nethogs, ss or a "
    "network namespace are privileged); a host-wide counter delta is a labelled approximation"
)


__all__ = [
    "NETWORK_NOT_COLLECTED",
    "ProcProbe",
    "PsProbe",
    "ResourceProbe",
    "ResourceSample",
    "degrade",
    "sample",
]
