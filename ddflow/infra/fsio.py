"""The file layer: one way to replace a file atomically and one way to lock one.

Every file ddflow writes that something else reads -- a config, an MCP server list, a hook,
a lease ledger -- must never be seen half-written, and two agents rewriting it at once must
not lose an edit. Both properties were re-implemented in several places with different
gaps (no fsync, a fixed temp name, a mode the file did not have before, a lock with no way
to stop waiting); this module is the one implementation (D-unify 4, B-uni-fsio), and its
callers move onto it slice by slice (the event log's lock in B-uni-fsio-log). The architecture
guards count `tempfile`, `fcntl` and `os.replace` outside this module and only let the
count go down.

POSIX only, as the rest of the package (`fcntl`).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import secrets
import stat
import time
from collections.abc import Iterator
from pathlib import Path

#: How often a lock with a timeout retries.
LOCK_POLL_S = 0.05


class LockTimeout(TimeoutError):
    """The lock was still held by someone else when the timeout ran out."""

    def __init__(self, lock: Path, timeout_s: float) -> None:
        super().__init__(f"{lock} is still locked after {timeout_s:g}s")
        self.lock = lock
        self.timeout_s = timeout_s


def _temp_beside(path: Path, perm: int) -> tuple[int, Path]:
    """A new, uniquely named file in `path`'s directory, open for writing, created with
    `perm` (less the umask). The name is unique per call: a FIXED name is shared by
    concurrent writers, and one renames the other's half-written file into place."""
    while True:
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, perm), tmp
        except FileExistsError:  # pragma: no cover - 48 random bits colliding
            continue


def atomic_write(
    path: Path | str,
    data: str | bytes,
    *,
    mode: int | None = None,
    exclusive: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Replace `path` with `data` so that no reader ever sees a partial file.

    The data goes to a unique temporary file in the same directory (one filesystem, so the
    rename is atomic), is flushed and fsync'd, and only then takes the real name. A crash
    at any point leaves either the old file or the new one, never an empty or torn one --
    a truncate-then-write (`write_text`) interrupted midway leaves an EMPTY config, which
    loads as "no overrides" with no error.

    `mode`: the new file's permission bits. By default an existing file keeps its own and a
    new one gets what `write_text` would give it (0666 less the umask). `exclusive`: refuse
    with `FileExistsError` when `path` already exists, instead of replacing it (a hard link
    of the finished temp file, so the check and the write are one step). On any failure the
    temporary file is removed and `path` is untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode(encoding) if isinstance(data, str) else data
    want = mode
    if want is None and not exclusive:
        with contextlib.suppress(FileNotFoundError):
            want = stat.S_IMODE(path.stat().st_mode)
    # With no mode to keep or set, 0666 less the umask: what `write_text` gives a new file
    # (`tempfile.mkstemp` makes 0600, and the rename would hand that to the real file).
    # With one, 0600 until the data is in, so a private file is never briefly readable.
    fd, tmp = _temp_beside(path, 0o666 if want is None else 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        if want is not None:
            os.chmod(tmp, want)
        if exclusive:
            os.link(tmp, path)
            tmp.unlink()
        else:
            os.replace(tmp, path)
    except BaseException:
        # Only this call's own temp file: unlinking any other is what let one writer
        # delete another's file in flight.
        tmp.unlink(missing_ok=True)
        raise


def lock_path_for(path: Path | str) -> Path:
    """The lock file that guards `path`: `.<name>.lock` beside it."""
    path = Path(path)
    return path.with_name(f".{path.name}.lock")


@contextlib.contextmanager
def file_lock(lock: Path | str, timeout_s: float | None = None) -> Iterator[None]:
    """Hold an exclusive `flock` on the file `lock` (created if missing) for the block.

    `timeout_s=None` waits as long as it takes; a number gives up with `LockTimeout` when
    someone else still holds it after that many seconds (0: one try). The lock is released
    when the block ends, by an exception too, and with the process if it dies.
    """
    lock = Path(lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if timeout_s is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + max(0.0, timeout_s)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(lock, timeout_s) from None
                    time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
