"""Perf metrics for a remote MCP server or a container (PM-remote).

One measuring wrapper (:func:`measure`) runs a command through a probe ladder
(``cgroup`` -> ``psutil`` -> ``proc`` -> ``wait4``), degrading as each source is
unavailable and recording WHICH source the numbers came from. A shell-less server never
runs the wrapper itself: the agent asks for the exact wrapped command
(:func:`wrap_command`, ``ddflow perf run -- CMD``), runs it, and posts the one JSON line
back through the ingest action (:func:`parse_line`). A sample the harness timed itself is
``agent-reported`` -- shown separately, stored at a lower trust level, and never counted as
gate evidence of a pass (D-perf-metrics-answers). Container status is one flag
(:func:`in_container`), so the env fingerprint can add container/VM runs to the key and
never mix a bare-metal series with a cgroup-limited one. Duration is measured on one side
only with a monotonic clock; no subtraction of two hosts' timestamps ever happens.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from .env import Fingerprint, in_container

UNAVAILABLE = "unavailable"

#: The probe ladder, most accurate first: :func:`measure` degrades through it and records
#: which source its resource numbers came from (a probe that could not run is named, never
#: passed off as a lower rung).
SOURCE_LADDER = ("cgroup", "psutil", "proc", "wait4")

#: ``ddflow`` ran the command itself through the wrapper: the only trust level whose sample
#: may ever be gate evidence.
TRUST_MEASURED = "measured"
#: The harness timed the run and reported it: shown separately, excluded from baselines and
#: budgets, never evidence of a pass.
TRUST_AGENT = "agent-reported"

ResourceProbe = Callable[[], "dict[str, float | None]"]


@dataclass(frozen=True)
class CommandSample:
    """One result of the measuring wrapper. ``wall_s`` is a monotonic wall clock on THIS
    host only; the resource numbers and their ``source`` record how they were read."""

    wall_s: float
    cpu_s: float | None
    rss_mb: float | None
    io_rw: float | None
    source: str
    trust: str = TRUST_MEASURED
    container: bool | None = None

    def one_line(self) -> str:
        """The single JSON line the wrapper prints (sorted keys: one shape everywhere)."""
        return json.dumps(
            {
                "wall_s": self.wall_s,
                "cpu_s": self.cpu_s,
                "rss_mb": self.rss_mb,
                "io_rw": self.io_rw,
                "source": self.source,
                "trust": self.trust,
                "container": self.container,
            },
            sort_keys=True,
        )


def wrap_command(cmd: str, *, program: str = "ddflow") -> str:
    """The exact wrapped command a shell-less server returns in its next-actions envelope:
    ``ddflow perf run -- CMD`` (the agent runs it; ddflow never guesses the shell)."""
    return f"{program} perf run -- {cmd}"


def measure(
    run: Callable[[], None],
    *,
    probe: Callable[[str], dict[str, float | None] | None],
    clock: Callable[[], float] = time.monotonic,
    container: Callable[[], bool | None] | None = None,
) -> CommandSample:
    """Run the command via ``run`` (blocking), timing it with ``clock`` (monotonic, this
    host only). ``probe`` takes a ladder rung and returns that rung's resource numbers
    (or ``None``/empty when the rung could not read them); the surviving rung is recorded
    as the source, so a probe that could not run is named, never passed off as a lower one."""
    start = clock()
    run()
    wall = clock() - start
    resource: dict[str, float | None] = {}
    source = UNAVAILABLE
    for rung in SOURCE_LADDER:
        got = probe(rung) or {}
        vals = {k: got.get(k) for k in ("cpu_s", "rss_mb", "io_rw")}
        if any(v is not None for v in vals.values()):
            resource = vals
            source = rung
            break
    ctr = container() if container is not None else None
    return CommandSample(
        wall_s=round(wall, 3),
        cpu_s=resource.get("cpu_s"),
        rss_mb=resource.get("rss_mb"),
        io_rw=resource.get("io_rw"),
        source=source,
        container=ctr,
    )


def parse_line(line: str) -> CommandSample:
    """Parse the single JSON line the wrapper (or an unwrapped agent run) reported. The
    trust level comes from the line itself: `measured` only when the wrapper wrote it, else
    `agent-reported` -- a caller cannot promote a self-reported line to `measured`."""
    data = json.loads(line)
    trust = TRUST_MEASURED if data.get("trust") == TRUST_MEASURED else TRUST_AGENT
    return CommandSample(
        wall_s=float(data["wall_s"]),
        cpu_s=data.get("cpu_s"),
        rss_mb=data.get("rss_mb"),
        io_rw=data.get("io_rw"),
        source=str(data.get("source") or UNAVAILABLE),
        trust=trust,
        container=data.get("container"),
    )


def fingerprint_container(fp: Fingerprint, *, in_ctr: bool | None, vm: bool | None = False) -> str:
    """The container/VM flags added to the env fingerprint key so a cgroup-limited series is
    never mixed with a bare-metal one, and a VM (a macOS Docker VM has no ``/.dockerenv``)
    never keys as bare metal. ``None`` means the probe could not run (named, never guessed)."""
    if in_ctr is None:
        flag = UNAVAILABLE
    elif in_ctr:
        flag = "container"
    elif vm:
        flag = "vm"
    else:
        flag = "bare"
    return f"{fp.stamp()} container={flag}"


__all__ = [
    "SOURCE_LADDER",
    "TRUST_AGENT",
    "TRUST_MEASURED",
    "UNAVAILABLE",
    "CommandSample",
    "fingerprint_container",
    "in_container",
    "measure",
    "parse_line",
    "wrap_command",
]
