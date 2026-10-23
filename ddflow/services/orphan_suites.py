"""Orphaned ci suites (Ba3b7509530 follow-up): `pre-commit run --hook-stage pre-push --all-files`
processes whose `ddflow` owner is gone.

`proc.run_shell` now kills a command's process group when its owner dies, but a suite orphaned
before that, or by a watcher that could not start, kept running for hours and starved every other
run. `doctor` names them; killing one stays the operator's decision, so this only reads /proc.

A process is an orphaned suite when its command line is the ci suite's, it is the caller's own,
it has run for at least ``min_age_s`` and none of its ancestors is a `ddflow` process (the chain
ends at init or a user `systemd`)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Orphan:
    pid: int
    pgid: int
    age_s: float
    cmd: str


def _stat(proc: Path, pid: int) -> tuple[int, int, int] | None:
    """(ppid, pgrp, starttime ticks) of ``pid``, or None when it is gone or unreadable."""
    try:
        text = (proc / str(pid) / "stat").read_text()
        fields = text.rsplit(")", 1)[1].split()
        return int(fields[1]), int(fields[2]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def _argv(proc: Path, pid: int) -> list[str]:
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [w.decode("utf-8", "replace") for w in raw.split(b"\0") if w]


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _is_suite(argv: list[str]) -> bool:
    """`pre-commit run --hook-stage pre-push --all-files` as the process's OWN argv: a shell
    that merely has the words in its `-c` string is not the suite (its child is)."""
    if not argv:
        return False
    head = argv[:3]
    if _base(head[0]) != "pre-commit" and not (
        "-m" in head and any(w.startswith("pre_commit") for w in argv[:4])
    ):
        return False
    stage = any(
        (a == "--hook-stage" and argv[i + 1 : i + 2] == ["pre-push"])
        or a == "--hook-stage=pre-push"
        for i, a in enumerate(argv)
    )
    return "run" in argv and stage and "--all-files" in argv


def _is_ddflow(argv: list[str]) -> bool:
    """A ddflow process: the `ddflow` / `ddflow-mcp` executable, or `python -m ddflow...`."""
    head = argv[:4]
    if any(_base(w) in ("ddflow", "ddflow-mcp") for w in head[:2]):
        return True
    return any(
        a == "-m" and head[i + 1 : i + 2] and head[i + 1].startswith("ddflow")
        for i, a in enumerate(head)
    )


def _has_ddflow_ancestor(proc: Path, pid: int) -> bool:
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        stat = _stat(proc, pid)
        if stat is None:
            return False
        pid = stat[0]
        if pid > 1 and _is_ddflow(_argv(proc, pid)):
            return True
    return False


def orphaned_ci_suites(
    proc: Path | str = "/proc",
    *,
    now: float | None = None,
    boot_time: float | None = None,
    uid: int | None = None,
    ticks: int | None = None,
    min_age_s: float = 600,
) -> list[Orphan]:
    """The caller's ci suites with no `ddflow` ancestor that have run for ``min_age_s``."""
    proc = Path(proc)
    now = time.time() if now is None else now
    uid = os.getuid() if uid is None else uid
    ticks = (
        (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100) if ticks is None else ticks
    )
    if boot_time is None:
        try:
            boot_time = float(
                next(
                    line.split()[1]
                    for line in (proc / "stat").read_text().splitlines()
                    if line.startswith("btime")
                )
            )
        except (OSError, StopIteration, ValueError, IndexError):
            return []
    found = []
    groups: set[int] = set()
    try:
        entries = sorted(int(d.name) for d in proc.iterdir() if d.name.isdigit())
    except OSError:
        return []
    for pid in entries:
        argv = _argv(proc, pid)
        if not _is_suite(argv):
            continue
        try:
            if (proc / str(pid)).stat().st_uid != uid:
                continue
        except OSError:
            continue
        stat = _stat(proc, pid)
        if stat is None:
            continue
        age = now - (boot_time + stat[2] / ticks)
        if age >= min_age_s and stat[1] not in groups and not _has_ddflow_ancestor(proc, pid):
            groups.add(stat[1])  # the shell and the suite it started are one finding
            found.append(Orphan(pid, stat[1], round(age, 1), " ".join(argv)))
    return found


def doctor_notes(orphans: list[Orphan]) -> list[str]:
    """One NOTE per orphaned suite, naming it and how to end it (the operator's decision)."""
    return [
        f"orphaned ci suite: pid {o.pid} (process group {o.pgid}) has run {o.age_s / 3600:.1f} h "
        f"with no ddflow process above it, so it starves other runs and loads the host; "
        f"`{o.cmd[:120]}` -- end it with `{_kill(o)}` once you have checked it is not wanted"
        for o in orphans
    ]


def _kill(o: Orphan) -> str:
    """`kill -- -PGID` for a real group; a group id of 1 or less is no group (`kill -- -1` would
    signal every process the user may), so then only the process itself."""
    return f"kill -- -{o.pgid}" if o.pgid > 1 else f"kill {o.pid}"
