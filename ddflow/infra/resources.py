"""Per-command resource capture: the one place that reads kernel rusage (PM-resource-capture).

`infra.proc` starts every shell line and argv run through `MeteredPopen`, a `Popen` whose reap
uses ``os.wait4`` instead of ``os.waitpid``: the kernel hands back THAT child's own rusage (user
and system cpu, peak rss, block io; a child that waited for its own descendants counts them
too). Where wait4 gave nothing (SIGCHLD ignored, no wait4) a before/after ``RUSAGE_CHILDREN``
delta stands in, marked ``delta`` because it is approximate (any child reaped meanwhile counts);
where neither exists (Windows) the answer is ``unavailable`` and every number is None, never 0.

Linux and macOS. ``ru_maxrss`` is KB on Linux and bytes on macOS: normalised to KB here.
Evidence fields: cpu_user_s, cpu_sys_s, maxrss_kb, io_in_blocks, io_out_blocks,
resources_source (wait4|delta|unavailable). Numbers only: no argument, path or output of the
command is ever recorded.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

try:  # POSIX only
    import resource as _resource
except ImportError:  # pragma: no cover - Windows
    _resource = None  # type: ignore[assignment]

WAIT4, DELTA, UNAVAILABLE = "wait4", "delta", "unavailable"

#: ru_maxrss is bytes on macOS, KB on Linux and the BSDs.
_MAXRSS_DIVISOR = 1024 if sys.platform == "darwin" else 1


def maxrss_kb(raw: int | float, platform: str | None = None) -> int:
    """``ru_maxrss`` as KB for ``platform`` (default: this host)."""
    div = (1024 if platform == "darwin" else 1) if platform is not None else _MAXRSS_DIVISOR
    return int(raw) // div


@dataclass(frozen=True)
class Resources:
    """What one command cost. Numbers are None (never 0) when ``source`` is unavailable."""

    source: str = UNAVAILABLE
    cpu_user_s: float | None = None
    cpu_sys_s: float | None = None
    maxrss_kb: int | None = None
    io_in_blocks: int | None = None
    io_out_blocks: int | None = None

    def evidence(self) -> dict[str, Any]:
        """The fields that join a gate's evidence."""
        return {
            "cpu_user_s": self.cpu_user_s,
            "cpu_sys_s": self.cpu_sys_s,
            "maxrss_kb": self.maxrss_kb,
            "io_in_blocks": self.io_in_blocks,
            "io_out_blocks": self.io_out_blocks,
            "resources_source": self.source,
        }


def from_rusage(ru: Any, source: str = WAIT4, platform: str | None = None) -> Resources:
    """A `Resources` from a ``struct_rusage`` (or anything with its attributes)."""
    return Resources(
        source,
        round(ru.ru_utime, 3),
        round(ru.ru_stime, 3),
        maxrss_kb(ru.ru_maxrss, platform),
        int(ru.ru_inblock),
        int(ru.ru_oublock),
    )


def metered(base: type) -> type:
    """A subclass of ``base`` (`subprocess.Popen`, handed in by `infra.proc`, the only module
    that may import subprocess) that keeps the kernel's rusage of its own reap in ``rusage``
    (None until reaped): the reap calls ``os.wait4`` instead of ``os.waitpid``."""

    class MeteredPopen(base):  # type: ignore[valid-type, misc]
        rusage: Any = None

        def _try_wait(self, wait_flags):  # type: ignore[no-untyped-def]
            wait4 = getattr(os, "wait4", None)
            if wait4 is None:
                return super()._try_wait(wait_flags)
            try:
                pid, sts, ru = wait4(self.pid, wait_flags)
            except ChildProcessError:  # SIGCHLD ignored: the status (and rusage) is gone
                return self.pid, 0
            if pid == self.pid:
                self.rusage = ru
            return pid, sts

    return MeteredPopen


def children_snapshot() -> Any:
    """``RUSAGE_CHILDREN`` now, or None where it does not exist."""
    if _resource is None:
        return None
    return _resource.getrusage(_resource.RUSAGE_CHILDREN)


def _delta(before: Any, after: Any, platform: str | None = None) -> Resources:
    class _D:  # maxrss of RUSAGE_CHILDREN is a high-water mark: the after value, not a difference
        ru_utime = max(0.0, after.ru_utime - before.ru_utime)
        ru_stime = max(0.0, after.ru_stime - before.ru_stime)
        ru_maxrss = after.ru_maxrss
        ru_inblock = max(0, after.ru_inblock - before.ru_inblock)
        ru_oublock = max(0, after.ru_oublock - before.ru_oublock)

    return from_rusage(_D, DELTA, platform)


def measure(p: Any, before: Any) -> Resources:
    """The cost of the reaped child ``p``: its wait4 rusage, else the delta since ``before``
    (a `children_snapshot` taken before it started), else unavailable."""
    ru = getattr(p, "rusage", None)
    if ru is not None:
        return from_rusage(ru)
    after = children_snapshot()
    if before is not None and after is not None:
        return _delta(before, after)
    return Resources()
