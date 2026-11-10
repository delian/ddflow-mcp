"""The `perf` command's API half: the measuring wrapper's env, run and ingest entry points.

``env`` prints this machine class's fingerprint (facts over the class of machine, never a
hostname, user or path); ``run`` runs the command through the measuring wrapper and prints
its one JSON line; ``ingest`` accepts one reported sample line and writes it to the ring.
An agent-reported sample is stored in its own series at the lower trust level and never
counted as gate evidence of a pass (D-perf-metrics-answers).
"""

from __future__ import annotations

import json
import resource
import subprocess
import time
from pathlib import Path

from ..core import outcome as O
from ..services.perf import env as E
from ..services.perf import remote as R
from ..services.perf import store
from ._base import _load


def _stdlib_probe(rung: str) -> dict[str, float | None] | None:
    """The stdlib rung of the probe ladder (``resource.ru`` = wait4); the cgroup/psutil/proc
    rungs need their sources and report ``None`` here so the ladder records ``wait4``."""
    if rung != "wait4":
        return None
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "cpu_s": ru.ru_utime + ru.ru_stime,
        "rss_mb": (ru.ru_maxrss / 1024.0) if ru.ru_maxrss else None,
        "io_rw": ru.ru_inblock + ru.ru_oublock,
    }


def run_perf_env(repo: Path, *, agent: str = "") -> O.Outcome:
    """The machine-class fingerprint (key, condition, facts, container flag)."""
    _, _cfg, _st = _load(repo, agent)
    fp = E.collect(repo, parallel_cap=None, xdist_workers=None)
    return O.ok("perf_env", text=fp.stamp())


def run_perf_run(repo: Path, *, cmd: str = "", agent: str = "") -> O.Outcome:
    """Run ``cmd`` through the measuring wrapper and return its one JSON line (monotonic
    wall, cpu, rss, io, the surviving probe rung). The wrapper is what ``perf wrap`` names."""
    _, _cfg, _st = _load(repo, agent)
    sample = R.measure(
        lambda: subprocess.run(cmd, shell=True, capture_output=True), probe=_stdlib_probe
    )
    return O.ok("perf_run", text=sample.one_line())


def run_perf_ingest(repo: Path, *, line: str = "", agent: str = "") -> O.Outcome:
    """Accept one reported sample line: an agent-reported timing is written to its own
    series (lower trust, never gate evidence); a malformed line is refused, never zero."""
    _, _cfg, _st = _load(repo, agent)
    try:
        sample = R.parse_line(line)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return O.refused("perf_ingest", "ingest needs the wrapper's one JSON line")
    series = "agent.wall" if sample.trust == R.TRUST_AGENT else "cmd.wall"
    fp = E.collect(repo, parallel_cap=None, xdist_workers=None)
    store.append(repo, series, store.Sample(t=time.time(), env=fp.key, value=sample.wall_s))
    return O.ok("perf_ingest", text=sample.one_line())
