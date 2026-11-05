"""Append-lock helpers: the re-entrant flock guard and holder introspection."""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from .. import fsio

#: Transaction depth per (pid, thread, lock path), NOT per EventLog instance.
#: `fcntl.flock` is per-open-file-description, so a second `os.open` of the same file
#: on the SAME thread blocks forever against the lock that thread already holds. Two
#: EventLog objects for one repo in one process is entirely reasonable and happens
#: today, so the guard has to be keyed on what actually identifies the lock AND the
#: thread: re-entry on the same thread is the deadlock case; a DIFFERENT thread is a
#: different writer and must park on the flock like another process (Bf7f6540615).
_HELD: dict[tuple[int, int, str], int] = {}


def _held_key(path: Path) -> tuple[int, int, str]:
    # threading.get_ident() values are unique only among LIVE threads and can be
    # recycled once a thread exits. This is safe because every release path cleans
    # `_HELD[key]` in a finally, so a recycled ident cannot meet a stale entry --
    # UNLESS a thread were killed mid-`transaction()` without its finally running
    # (pthread_cancel-style death, unreachable from Python exceptions or any managed
    # code path here). Transactions must therefore never be abandoned mid-hold by a
    # killed thread; there is no reclamation for that, by design.
    return (os.getpid(), threading.get_ident(), str(path))


def _holder_note(path: Path) -> str:
    """Who holds the flock on `path` right now, from the kernel; '' when it cannot say.

    flock cannot say who owns a lock, but Linux lists every flock in /proc/locks with the
    holder's pid and the file's device and inode. Asking the kernel costs the hot path
    nothing and writes nothing: an earlier design had each holder write a note INTO the
    lock file, which put per-writer bytes into a file that an un-adopted project commits
    (no `.ddflow/.gitignore` yet), where two clones' notes conflict on merge.

    Best effort and never raises: this runs while a TimeoutError is being built. Other
    platforms (no /proc/locks) and a lock released meanwhile give ''; a lock still held
    whose holder is not in /proc/locks (another pid namespace, or a scan that kept missing
    it) gives 'Holder unknown (...)', never silence.
    """
    try:
        st = os.stat(path)
        want = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        if not Path("/proc/locks").exists():
            return ""
    except OSError:
        return ""
    # /proc/locks is served in several chunks and is not a snapshot: under heavy flock
    # traffic (a parallel test run) entries move between chunks and a live holder is
    # skipped by a single read (bug Bf540195aec; ~14% of scans missed it in a
    # reproduction). So scan again, within a small budget, before giving up.
    t0 = time.monotonic()
    deadline = t0 + _HOLDER_SCAN_S
    scans = 0
    while True:
        scans += 1
        try:
            pid = _locks_holder(want)
        except (OSError, ValueError):
            # could not read the table: say so if the lock is still held, else say nothing
            return " Holder unknown (/proc/locks unreadable)." if _still_locked(path) else ""
        if pid:
            try:
                return f" Held by {_describe_pid(pid)}."
            except (OSError, ValueError):
                return f" Held by pid {pid}."
        if not _still_locked(path):
            return ""  # released meanwhile: there is no holder to name
        if scans >= _HOLDER_SCANS_MAX or time.monotonic() >= deadline:
            return f" Holder unknown (not in /proc/locks after {scans} scans in {time.monotonic() - t0:.1f}s)."
        time.sleep(0.01)


def _still_locked(path: Path) -> bool:
    """Is the flock on `path` held by someone right now? (a non-blocking probe)"""
    try:
        with fsio.file_lock(path, 0, poll_s=0.01):
            return False
    except fsio.LockTimeout:
        return True
    except OSError:
        return True  # could not probe: keep the 'unknown' conclusion, not 'free'


def _locks_holder(want: str) -> int:
    """The pid holding the flock on `major:minor:inode` per one read of /proc/locks, or 0."""
    for line in Path("/proc/locks").read_text().splitlines():
        f = line.split()
        # `1: FLOCK ADVISORY WRITE 4242 fd:00:131077 0 EOF`.
        if any("->" in tok for tok in f[:2]):
            continue  # a blocked waiter (the kernel prefixes it), never the holder
        if len(f) >= _LOCK_FIELDS and f[1] == "FLOCK" and f[5] == want:
            pid = int(f[4])
            if pid > 0:
                return pid
    return 0


#: Rescans of /proc/locks when the holder is not in the first read, and the time they get.
_HOLDER_SCANS_MAX = 50
_HOLDER_SCAN_S = 1.0


#: Fields on a /proc/locks line up to and including the `major:minor:inode` column.
_LOCK_FIELDS = 6


#: How much of a holder's command line a lock-timeout message shows: its first
#: _CMD_SHOWN characters, as always, and past that its last _CMD_TAIL.
_CMD_SHOWN = 200
_CMD_TAIL = 60
#: An interpreter whose first argument is the script it runs: `python3`, `python3.13`, and
#: the build-suffixed `python3.13t` (free-threaded), `python3.13d`, `python3-dbg`.
_PYTHON = re.compile(r"python[0-9.]*[a-z]?(-dbg)?")


def _describe_pid(pid: int) -> str:
    """`pid N (command line)` -- the command from /proc, or just the pid.

    The interpreter (and a script it runs) is shown by its base name: a venv under a deep
    directory filled the whole budget with its path, cutting off the arguments that say
    WHAT holds the lock (bug B3a4bf051b4). A line still too long keeps its first
    _CMD_SHOWN characters and gains its tail, so nothing shown before is lost.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return f"pid {pid}"
    argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    if argv:
        argv[0] = os.path.basename(argv[0]) or argv[0]
    if len(argv) > 1 and _PYTHON.fullmatch(argv[0]) and argv[1].startswith("/"):
        argv[1] = os.path.basename(argv[1]) or argv[1]  # the script it runs, never an argument
    cmd = " ".join(argv).strip()
    if len(cmd) > _CMD_SHOWN + _CMD_TAIL + 3:
        cmd = f"{cmd[:_CMD_SHOWN]}...{cmd[-_CMD_TAIL:]}"
    return f"pid {pid} ({cmd})" if cmd else f"pid {pid}"


@contextlib.contextmanager
def _flock(path: Path, timeout_s: float) -> Iterator[None]:
    """Exclusive advisory lock, with a bounded wait and a real error on timeout.

    The lock file is created once and NEVER atomically replaced: renaming over a lock
    file puts two holders on two different inodes, each believing it is exclusive. It is
    also never WRITTEN: its content is empty, so a project that commits it (before
    `.ddflow/.gitignore` exists) cannot get a merge conflict from it.

    A timeout says how long this process waited and, on Linux, who holds the lock (B184:
    a timeout under machine load was suspected and could not be told from a wedged agent).
    """
    started = time.monotonic()
    with contextlib.ExitStack() as held:
        try:  # only the acquisition: a timeout raised inside the block is not ours
            held.enter_context(fsio.file_lock(path, timeout_s, poll_s=0.02))
        except fsio.LockTimeout:
            raise TimeoutError(
                f"could not acquire {path} within {timeout_s}s "
                f"(waited {time.monotonic() - started:.1f}s, this is pid {os.getpid()})."
                f"{_holder_note(path)} Another agent may be wedged, or the machine is "
                f"overloaded -- check `ddflow doctor`"
            ) from None
        yield
