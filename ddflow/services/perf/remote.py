"""Perf metrics for a remote MCP server or a container (PM-remote).

One measuring wrapper (:func:`measure`) runs a command through a probe ladder
(``cgroup`` -> ``psutil`` -> ``proc`` -> ``wait4``), degrading as each source is
unavailable and recording WHICH source the numbers came from. A shell-less server never
runs the wrapper itself: the agent asks for the exact wrapped command
(:func:`wrap_command`, ``ddflow perf run --cmd CMD``), runs it, and posts the one JSON line
back through the ingest action (:func:`parse_line`). A sample the harness timed itself is
``agent-reported`` -- shown separately, stored at a lower trust level, and never counted as
gate evidence of a pass (D-perf-metrics-answers). Container status is one flag
(:func:`in_container`), so the env fingerprint can add container/VM runs to the key and
never mix a bare-metal series with a cgroup-limited one. Duration is measured on one side
only with a monotonic clock; no subtraction of two hosts' timestamps ever happens.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass

from ddflow.adapters.clock import DEFAULT as CLOCK

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
    host only; the resource numbers and their ``source`` record how they were read.
    ``env`` carries the wrapper's own fingerprint key, so an ingested sample keeps the
    machine class it ran on (never re-stamped with the receiving server's)."""

    wall_s: float
    cpu_s: float | None
    rss_mb: float | None
    io_rw: float | None
    source: str
    trust: str = TRUST_MEASURED
    container: bool | None = None
    env: str = ""

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
                "env": self.env,
            },
            sort_keys=True,
        )


def wrap_command(cmd: str, *, program: str = "ddflow") -> str:
    """The exact wrapped command a shell-less server returns in its next-actions envelope:
    ``ddflow perf run --cmd CMD`` with ``CMD`` shell-quoted, so the single ``--cmd`` flag
    binds the whole command as one token (the agent runs it verbatim; ddflow never guesses
    the shell)."""
    return f"{program} perf run --cmd {shlex.quote(cmd)}"


def measure(
    run: Callable[[], None],
    *,
    probe: Callable[[str], dict[str, float | None] | None],
    clock: Callable[[], float] = CLOCK.monotonic,
    container: Callable[[], bool | None] | None = None,
    env_key: str = "",
) -> CommandSample:
    """Run the command via ``run`` (blocking), timing it with ``clock`` (monotonic, this
    host only). ``probe`` takes a ladder rung and returns that rung's resource numbers
    (or ``None``/empty when the rung could not read them); the surviving rung is recorded
    as the source, so a probe that could not run is named, never passed off as a lower one.
    ``env_key`` is the machine-class digest the sample keeps (never a hostname or path)."""
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
        env=env_key,
    )


def parse_line(line: str) -> CommandSample:
    """Parse the single JSON line an agent reported. Every ingested line is
    ``agent-reported`` -- a posted line cannot prove it came from the wrapper, so the trust
    field a caller supplies is ignored and a self-reported time is never gate evidence
    (D-perf-metrics-answers). Only :func:`measure` (ddflow runs the command itself) ever
    produces ``measured``."""
    data = json.loads(line)
    return CommandSample(
        wall_s=float(data["wall_s"]),
        cpu_s=_maybe_float(data.get("cpu_s")),
        rss_mb=_maybe_float(data.get("rss_mb")),
        io_rw=_maybe_float(data.get("io_rw")),
        source=str(data.get("source") or UNAVAILABLE),
        trust=TRUST_AGENT,
        container=data.get("container"),
        env=_valid_env_key(str(data.get("env") or "")),
    )


def _maybe_float(value: object) -> float | None:
    """``float``, or ``None`` for a missing or non-numeric field. An ingested line's resource
    fields are coerced here, never let through as strings (a budgets/rollup that adds them
    would mix types, or ``one_line`` round-trip the bad type as JSON)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_env_key(env: str) -> str:
    """An env key is a machine-class digest, never a hostname, user or path. A posted line
    must not inject identity, so any key carrying a path/address marker, or that is not the
    digest the wrapper emits (12 hex chars, optionally ``.bare|container|vm|unavailable``),
    is dropped."""
    if not env:
        return ""
    if any(c in env for c in ("/", "\\", ":", "@", " ", "\n")):
        return ""
    if not re.fullmatch(r"[0-9a-f]{12}(?:\.(?:bare|container|vm|unavailable))?", env):
        return ""
    return env


def fingerprint_container(fp: Fingerprint, *, in_ctr: bool | None, vm: bool | None = None) -> str:
    """The container/VM flag appended to the env fingerprint KEY so a cgroup-limited series is
    never mixed with a bare-metal one, and a VM (a macOS Docker VM has no ``/.dockerenv``)
    never keys as bare metal. ``None`` for either flag means the probe could not run
    (``unavailable``, never guessed). The result is still a valid env key: the flag is joined
    to ``fp.key`` with ``.``, never the ``stamp()`` (whose spaces ``_valid_env_key`` drops)."""
    if in_ctr is None:
        flag = UNAVAILABLE
    elif in_ctr:
        flag = "container"
    elif vm is None:
        flag = UNAVAILABLE
    elif vm:
        flag = "vm"
    else:
        flag = "bare"
    return f"{fp.key}.{flag}"


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
