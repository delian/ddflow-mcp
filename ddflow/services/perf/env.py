"""Environment fingerprint stamped on perf samples and gate/ci evidence.

A timing series is noise unless it is keyed by where it ran. :func:`fingerprint` is pure:
it takes already-probed facts and returns a short stable key over the *class* of
machine (never hostname, user or path) plus the facts it was built from. :func:`collect`
probes this machine with every reader injectable, so tests use fakes. A probe that
could not run is the string ``unavailable``, never zero and never a pass.

The git head of the measured tree is stamped but is NOT part of the key: it changes
every commit, and the regression detector compares samples of one key class across
commits. Thresholds (load bands) are parameters here; the ``[perf_metrics]`` knob
section that feeds them lands with the perf config task.

Next call: the stamp is read back with ``ddflow perf env`` / MCP ``ddflow_perf action=env``
once the perf CLI task registers it.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ddflow import __version__
from ddflow.core.digest import of_obj
from ddflow.infra.worktree._merge import head_sha

UNAVAILABLE = "unavailable"
DEFAULT_LOAD_BANDS: tuple[float, float] = (0.25, 1.0)
KEY_LEN = 12
#: cgroup v1 reports "no limit" as a number near 2**63; anything below 2**60 is a real cap.
V1_UNLIMITED_BYTES = 2**60
#: Facts that change between runs on one machine: they form `cond`, not the key.
CONDITION_FACTS = ("load", "cache")
HEAD_LEN = 12

Reader = Callable[[str], "str | None"]


@dataclass(frozen=True)
class Fingerprint:
    """``key`` names the machine class (stable across runs and commits); ``cond`` names the
    run-time condition (load class, cache state). Compare series on ``key``; also require an
    equal ``cond`` when load or cache could explain a difference."""

    key: str
    cond: str = ""
    facts: dict[str, object] = field(default_factory=dict)

    def stamp(self) -> str:
        """One line for evidence: the key then the readable facts."""
        parts = " ".join(f"{k}={v}" for k, v in sorted(self.facts.items()))
        return f"env:{self.key}/{self.cond} {parts}"


def load_class(ratio: float | None, bands: tuple[float, float] = DEFAULT_LOAD_BANDS) -> str:
    """Load per cpu at start: idle below bands[0], busy below bands[1], else saturated."""
    if ratio is None:
        return UNAVAILABLE
    return "idle" if ratio < bands[0] else "busy" if ratio < bands[1] else "saturated"


def _read_file(path: str) -> str | None:
    try:
        return Path(path).read_text("utf-8", errors="replace")
    except OSError:
        return None


def in_container(exists: Callable[[str], bool] | None = None) -> bool:
    ex = exists or (lambda p: Path(p).exists())
    return ex("/.dockerenv") or ex("/run/.containerenv")


def cgroup_state(read: Reader = _read_file, v1_unlimited_bytes: int = V1_UNLIMITED_BYTES) -> str:
    """limited | unlimited | unavailable, from cgroup v2 then v1 cpu and memory limits."""
    seen: list[bool] = []
    cpu = read("/sys/fs/cgroup/cpu.max")
    if cpu is not None and cpu.split():
        seen.append(cpu.split()[0] != "max")
    mem = read("/sys/fs/cgroup/memory.max")
    if mem is not None and mem.strip():
        seen.append(mem.strip() != "max")
    v1mem = read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if v1mem is not None and v1mem.strip().isdigit():
        seen.append(int(v1mem.strip()) < v1_unlimited_bytes)
    quota = read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    if quota is not None and quota.strip():
        seen.append(quota.strip() != "-1")
    if not seen:
        return UNAVAILABLE
    return "limited" if any(seen) else "unlimited"


def cache_state(dirs: Iterable[Path]) -> str:
    """warm when any given cache directory holds an entry, cold when none does."""
    ds = list(dirs)
    if not ds:
        return UNAVAILABLE
    unreadable = False
    for d in ds:
        try:
            with os.scandir(d) as it:
                if any(True for _ in it):
                    return "warm"
        except (FileNotFoundError, NotADirectoryError):
            continue  # no such cache: that is what cold means
        except OSError:
            unreadable = True  # exists but cannot be looked at: the probe did not run
    return UNAVAILABLE if unreadable else "cold"


def _num(v: int | None) -> object:
    return UNAVAILABLE if v is None else v


@dataclass(frozen=True)
class Raw:
    """Already-probed facts; ``None`` or empty means the probe could not run."""

    cpu_count: int | None
    load_ratio: float | None
    xdist_workers: int | None
    parallel_cap: int | None
    system: str
    python_version: str
    ddflow_version: str
    git_head: str
    cache: str | None
    container: bool
    cgroup: str


def fingerprint(
    raw: Raw | None = None,
    *,
    load_bands: tuple[float, float] = DEFAULT_LOAD_BANDS,
    **kw: object,
) -> Fingerprint:
    r = raw or Raw(**kw)  # type: ignore[arg-type]
    cpu_count, load_ratio, system = r.cpu_count, r.load_ratio, r.system
    xdist_workers, parallel_cap = r.xdist_workers, r.parallel_cap
    python_version, ddflow_version = r.python_version, r.ddflow_version
    git_head, cache, container = r.git_head, r.cache, r.container
    cgroup = r.cgroup or UNAVAILABLE
    vm = "unknown" if system == "Darwin" or (container and cgroup == UNAVAILABLE) else "no"
    load, cache_fact = load_class(load_ratio, load_bands), cache or UNAVAILABLE
    facts: dict[str, object] = {
        "cpu_count": _num(cpu_count),
        "load": load,
        "xdist_workers": _num(xdist_workers),
        "parallel_cap": _num(parallel_cap),
        "os": system or UNAVAILABLE,
        "python": ".".join(python_version.split(".")[:2]) or UNAVAILABLE,
        "cache": cache_fact,
        "ddflow": ddflow_version or UNAVAILABLE,
        "container": container,
        "cgroup": cgroup,
        "vm": vm,
    }
    cond = of_obj({k: facts.pop(k) for k in CONDITION_FACTS}, length=KEY_LEN)
    key = of_obj(facts, length=KEY_LEN)
    facts.update(load=load, cache=cache_fact)
    facts["git_head"] = git_head[:HEAD_LEN] if git_head else UNAVAILABLE
    return Fingerprint(key, cond, facts)


def _git_head(root: Path) -> str:
    try:
        return head_sha(root)
    except Exception:
        return ""


def collect(
    root: Path,
    *,
    parallel_cap: int | None,
    xdist_workers: int | None,
    cache_dirs: Iterable[Path] = (),
    load_bands: tuple[float, float] = DEFAULT_LOAD_BANDS,
    read: Reader = _read_file,
    exists: Callable[[str], bool] | None = None,
    git_head: str | None = None,
) -> Fingerprint:
    """Probe this machine. Every probe that fails is ``unavailable``."""
    cpus = os.cpu_count()
    try:
        ratio = os.getloadavg()[0] / cpus if cpus else None
    except (OSError, AttributeError):
        ratio = None
    raw = Raw(
        cpu_count=cpus,
        load_ratio=ratio,
        xdist_workers=xdist_workers,
        parallel_cap=parallel_cap,
        system=platform.system(),
        python_version=sys.version.split()[0],
        ddflow_version=__version__,
        git_head=_git_head(root) if git_head is None else git_head,
        cache=cache_state(cache_dirs),
        container=in_container(exists),
        cgroup=cgroup_state(read),
    )
    return fingerprint(raw, load_bands=load_bands)
