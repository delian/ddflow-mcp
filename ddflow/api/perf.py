"""The `perf` command's API half: the measuring wrapper's env, run and ingest entry points.

``env`` prints this machine class's fingerprint (facts over the class of machine, never a
hostname, user or path); ``run`` runs the command through the measuring wrapper and prints
its one JSON line; ``ingest`` accepts one reported sample line and writes it to the ring.
An agent-reported sample is stored in its own series at the lower trust level and never
counted as gate evidence of a pass (D-perf-metrics-answers).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

from ..core import outcome as O
from ..infra import proc as P
from ..services.perf import env as E
from ..services.perf import remote as R
from ..services.perf import store
from ._base import _load


def _rss_mb(ru) -> float | None:
    """``ru`` is a ``resource.struct_rusage``; ``ru_maxrss`` is KB on Linux and bytes on
    macOS, scaled to MB per platform so one wrapper reports the same number on the two sides
    the fingerprint distinguishes."""
    if not ru.ru_maxrss:
        return None
    return ru.ru_maxrss / 1048576.0 if sys.platform == "darwin" else ru.ru_maxrss / 1024.0


def run_perf_env(repo: Path, *, agent: str = "") -> O.Outcome:
    """The machine-class fingerprint (key, condition, facts, container flag)."""
    _, _cfg, _st = _load(repo, agent)
    fp = E.collect(repo, parallel_cap=None, xdist_workers=None)
    return O.ok("perf_env", text=fp.stamp())


def run_perf_run(repo: Path, *, cmd: str = "", agent: str = "") -> O.Outcome:
    """Run ``cmd`` (an argv string, split with the shell rules) through the measuring wrapper
    and return its one JSON line, or a refused/failed outcome when it could not run or exited
    non-zero. The resource numbers come from THIS child's own ``wait4`` rusage (per-child, not
    the process-global ``RUSAGE_CHILDREN`` aggregate); duration is a monotonic clock on this
    host; ``source`` is the ladder rung the numbers came from. The env key is the receiver's
    own machine class."""
    _, _cfg, _st = _load(repo, agent)
    if not cmd.strip():
        return O.refused("perf_run", "a command to measure")
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return O.refused("perf_run", "a well-quoted command")
    child: dict[str, float | None] = {}
    ran: dict[str, int] = {}
    missing: dict[str, str] = {}

    def run() -> None:
        try:
            proc = P.Popen(argv, stdout=P.DEVNULL, stderr=P.DEVNULL)
        except OSError:
            missing["argv0"] = argv[0] if argv else "?"
            raise
        try:
            _, status, ru = os.wait4(proc.pid, 0)
        except OSError:
            missing["argv0"] = argv[0] if argv else "?"
            raise
        if os.WIFSIGNALED(status):
            ran["returncode"] = -os.WTERMSIG(status)
        else:
            ran["returncode"] = os.WEXITSTATUS(status)
        proc.returncode = ran["returncode"]
        child.update(
            {
                "cpu_s": ru.ru_utime + ru.ru_stime,
                "rss_mb": _rss_mb(ru),
                "io_rw": ru.ru_inblock + ru.ru_oublock,
            }
        )

    def probe(rung: str) -> dict[str, float | None] | None:
        return child if rung == "wait4" else None

    try:
        sample = R.measure(run, probe=probe, env_key=_fingerprint_key(repo))
    except OSError:
        return O.refused("perf_run", f"could not run: {missing.get('argv0', '?')}")
    if ran["returncode"] != 0:
        return O.failed("perf_run", f"command exited {ran['returncode']}", text=sample.one_line())
    return O.ok("perf_run", text=sample.one_line())


def _fingerprint_key(repo: Path) -> str:
    return E.collect(repo, parallel_cap=None, xdist_workers=None).key


def run_perf_ingest(repo: Path, *, line: str = "", agent: str = "") -> O.Outcome:
    """Accept one reported sample line: an agent-reported timing is written to its own
    series (lower trust, never gate evidence); a malformed line is refused, never zero."""
    _, _cfg, _st = _load(repo, agent)
    try:
        sample = R.parse_line(line)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return O.refused("perf_ingest", "ingest needs the wrapper's one JSON line")
    series = (
        "agent" if sample.trust == R.TRUST_AGENT else "cmd"
    ) + f".{(agent or 'role').replace('.', '_')}"
    # the sample keeps the machine class it ran on (the line's own env when the wrapper
    # produced it); a line with no env is unknown origin, never re-stamped with the
    # receiving server's fingerprint
    env_key = sample.env or R.UNAVAILABLE
    for metric, value in (
        ("wall", sample.wall_s),
        ("cpu", sample.cpu_s),
        ("rss", sample.rss_mb),
        ("io", sample.io_rw),
    ):
        if value is not None:
            store.append(
                repo, f"{series}.{metric}", store.Sample(t=time.time(), env=env_key, value=value)
            )
    return O.ok("perf_ingest", text=sample.one_line())
