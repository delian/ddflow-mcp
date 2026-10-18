"""The pytest worker count, computed when the tests run (D-agent-test-etiquette).

``workers = clamp(floor(cores * load_high - loadavg), [ci].min_workers, [ci].max_workers)``,
cut when memory is short and never above the number of test files; ``[ci].workers = N``
fixes it. The inputs come from ``infra/signals`` and are returned with the answer so the
gate evidence can show how it was reached. A command spells the count ``{workers}``."""

from __future__ import annotations

import math
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from ..infra import signals as S

PLACEHOLDER = "{workers}"
#: `shlex.join` quotes the braces, so a joined command carries the quoted form.
_FORMS = (f"'{PLACEHOLDER}'", f'"{PLACEHOLDER}"', PLACEHOLDER)  # quoted first
_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py(?:::.*)?$")
#: Without a load reading the count is the old fixed ceiling agents were held to.
UNKNOWN_LOAD_WORKERS = 16


@dataclass(frozen=True)
class Workers:
    n: int
    inputs: dict[str, Any] = field(default_factory=dict)


def _mark(cfg: Any, signal: str, name: str, default: float | None) -> float | None:
    marks = (getattr(cfg.schedule, "signals", None) or {}).get(signal) or {}
    value = marks.get(name, default)
    return float(value) if isinstance(value, (int, float)) else default


def compute(cfg: Any, files: int | None = None, *, source: S.SignalSource | None = None,
            cores: int | None = None) -> Workers:  # fmt: skip
    """The worker count for ``files`` test files on this host, now, with its inputs."""
    ci = cfg.ci
    lo, hi = max(1, int(ci.min_workers)), max(1, int(ci.max_workers))
    hi = max(lo, hi)
    setting = str(ci.workers).strip().lower() or "auto"
    inputs: dict[str, Any] = {"setting": setting, "min": lo, "max": hi, "files": files}
    if setting != "auto":
        n = int(setting) if setting.isdigit() and int(setting) > 0 else 1
        return Workers(n, {**inputs, "rule": "fixed by [ci].workers"})
    cores = cores or os.cpu_count() or 1
    values = (source or S.HostSignals()).sample()
    load, free = values.get("load"), values.get("memory_free_frac")
    high = _mark(cfg, "load_per_core", "high", 0.75) or 0.75
    inputs.update(cores=cores, load_high=high, load_per_core=load, memory_free_frac=free)
    if load is None:
        n = max(lo, min(hi, UNKNOWN_LOAD_WORKERS))
        inputs["rule"] = f"load unavailable: at most {UNKNOWN_LOAD_WORKERS}"
    else:
        loadavg = load * cores
        inputs["loadavg"] = round(loadavg, 2)
        n = max(lo, min(hi, math.floor(cores * high - loadavg)))
        inputs["rule"] = "floor(cores * load_high - loadavg), clamped"
    if free is not None:
        pressure = 1.0 - free
        crit = _mark(cfg, "memory_pressure", "critical", 0.95)
        warn = _mark(cfg, "memory_pressure", "high", 0.85)
        if crit is not None and pressure >= crit:
            n, inputs["memory"] = lo, "critical: minimum"
        elif warn is not None and pressure > warn:
            n, inputs["memory"] = max(lo, n // 2), "high: halved"
    if files is not None:
        n = min(n, files)
    n = max(1, n)
    return Workers(n, inputs)


#: Options whose value is a path to leave OUT or look at, never a test to run.
_NOT_SELECTED = frozenset({"--ignore", "--ignore-glob", "--deselect", "-c", "--rootdir",
                           "--confcutdir", "-p", "-o", "-k", "-m", "--junitxml"})  # fmt: skip


def count_test_files(command: str) -> int | None:
    """How many distinct test files ``command`` selects; None when it names none (a
    directory run). An option's value (`--ignore=tests/test_x.py`) is not a selection."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    found: set[str] = set()
    for i, t in enumerate(tokens):
        if t.startswith("-") or (i > 0 and tokens[i - 1] in _NOT_SELECTED):
            continue
        if _TEST_FILE.search(t):
            found.add(t.partition("::")[0])
    return len(found) or None


def uses(command: str) -> bool:
    return any(f in command for f in _FORMS)


def resolve(command: str, cfg: Any, files: int | None = None, *, source: Any = None,
            cores: int | None = None) -> tuple[str, Workers | None]:  # fmt: skip
    """``command`` with ``{workers}`` replaced; the Workers used (None: it had none)."""
    if not uses(command):
        return command, None
    w = compute(cfg, files if files is not None else count_test_files(command), source=source,
                cores=cores)  # fmt: skip
    for form in _FORMS:
        command = command.replace(form, str(w.n))
    return command, w
