"""The `perf` command's API half: the measuring wrapper's env and ingest entry points.

``env`` prints this machine class's fingerprint (facts over the class of machine, never a
hostname, user or path); ``run`` returns the exact wrapped command an agent runs where
ddflow cannot (a shell-less server, a container); ``ingest`` accepts one reported sample
line. An agent-reported sample is stored at the lower trust level and never counted as
gate evidence of a pass (D-perf-metrics-answers).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core import outcome as O
from ..services.perf import env as E
from ..services.perf import remote as R
from ._base import _load


def run_perf_env(repo: Path, *, agent: str = "") -> O.Outcome:
    """The machine-class fingerprint (key, condition, facts, container flag)."""
    _, _cfg, _st = _load(repo, agent)
    fp = E.collect(repo, parallel_cap=None, xdist_workers=None)
    return O.ok("perf_env", text=fp.stamp())


def run_perf_run(repo: Path, *, agent: str = "") -> O.Outcome:
    """The exact wrapped command an agent runs where ddflow cannot run it itself."""
    _, _cfg, _st = _load(repo, agent)
    return O.ok("perf_run", text=R.wrap_command("CMD"))


def run_perf_ingest(repo: Path, *, line: str = "", agent: str = "") -> O.Outcome:
    """Accept one reported sample line (the wrapper's one JSON line, or an agent-reported
    timing). Malformed input is refused, never recorded as a zero."""
    _, _cfg, _st = _load(repo, agent)
    try:
        sample = R.parse_line(line)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return O.refused("perf_ingest", "ingest needs the wrapper's one JSON line")
    return O.ok("perf_ingest", text=sample.one_line())
