"""The `Fsio` port and its default adapter: atomic writes, temporary files and locks.

`tempfile` and `fcntl` are imported here and nowhere else once the call sites move in
(ADP-migrate-homes); the `fsio-home` import contract only lets the sites that still import
them shrink. Atomic text writes, temporary files and locks are the Fsio family; the doc-region
`Managed`/`Region` stamping and the JSON/path helpers stay in `ddflow.infra.fsio`, which
re-exports this module's names.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port

#: Re-exported so the fake (`fakes.fsio`) needs only import this module, not `tempfile`.
TemporaryDirectory = tempfile.TemporaryDirectory


@port
@runtime_checkable
class Fsio(Protocol):
    """Atomic writes, temporary files and locks in one home.

    Home: `ddflow.adapters.fsio` (compat re-export `ddflow.infra.fsio`). Single adapter.
    """

    def atomic_write(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Write `text` all-or-nothing, beside the target then renamed over it."""

    def replace_text(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Rewrite `path` in place only when its content actually changed."""

    def file_lock(self, path: Path | str, *, timeout: float | None = None) -> Iterator[Any]:
        """An exclusive lock for `path`, released on exit; raises `LockTimeout`."""

    def scratch_dir(self) -> Any:
        """A temporary directory that removes itself on exit."""

    def temp_text(self, text: str, suffix: str = "") -> Iterator[Path]:
        """`text` in a temporary file, removed on exit."""


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
    fsync: bool = True,
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
    of the finished temp file, so the check and the write are one step). On a filesystem
    with no hard links (FAT, many SMB and FUSE mounts) an exclusive write is created with
    `O_EXCL` and written in place: still never replacing a file, but NOT atomic for a
    reader there, and a failure removes what it created. Otherwise, on any failure the
    temporary file is removed and `path` is untouched. `fsync=False` skips the flush to
    disk: still never torn for a reader, but a crash may lose the new content -- for a
    cache that is rebuilt anyway.
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
            if fsync:
                os.fsync(fh.fileno())
        if want is not None:
            os.chmod(tmp, want)
        if exclusive:
            _link_exclusive(tmp, path, raw, want, fsync=fsync)
        else:
            os.replace(tmp, path)
    except BaseException:
        # Only this call's own temp file: unlinking any other is what let one writer
        # delete another's file in flight. Best effort, so the error raised is the
        # write's own, never the cleanup's.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    if exclusive:
        # The file is in place: the write HAPPENED, and a temp name that cannot be
        # removed (it is unique, and harmless) must not report it failed (B4dd9658753).
        with contextlib.suppress(OSError):
            tmp.unlink()


@contextlib.contextmanager
def atomic_file(path: Path | str, *, mode: int | None = None, fsync: bool = True) -> Iterator[Any]:
    """A seekable binary file that becomes `path` when the block ends without exception:
    `atomic_write` for content no caller should hold whole at once.

    The yielded handle is this call's own temporary file in `path`'s directory, so the
    writer may seek back over what it wrote (a header whose size and digest only settle
    after the payload has streamed). Any exception removes the temporary file and leaves
    `path` untouched, and publishing is one rename -- a reader sees the old file or the
    new one, never half of either. `mode` and `fsync` mean what they mean in
    `atomic_write`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    want = mode
    if want is None:
        with contextlib.suppress(FileNotFoundError):
            want = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = _temp_beside(path, 0o666 if want is None else 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            yield fh
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        if want is not None:
            os.chmod(tmp, want)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def replace_text(path: Path | str, text: str, *, fsync: bool = True) -> None:
    """`Path(path).write_text(text, "utf-8")` that no reader ever sees half-written.

    The drop-in for a writer moving off `write_text` with nothing else changing: a
    symlink is written THROUGH (its target gets the new text and the link stays, where
    `atomic_write` alone would put a regular file in the link's place -- `CLAUDE.md ->
    AGENTS.md` is a common layout); a missing parent, a parent that is a file, or a link
    loop raises what `write_text` raises, and nothing is created. A new file gets 0666
    less the umask and an existing one keeps its mode, as with `write_text`.

    Where only an in-place write keeps `write_text`'s result, the file is written in
    place, as `write_text` did, and so NOT atomically: a file with other hard links (they
    share the inode, and a rename would leave them stale), one owned by another user (a
    rename would take it over), one the caller may not write (`write_text` refuses it,
    a rename would not), and, when the rename itself is refused (EACCES, EPERM, EBUSY), a
    writable file in a directory the caller may not write or a bind-mounted single file.
    `fsync` still applies there: the written descriptor is flushed to disk.
    """
    named = Path(path)
    target = Path(os.path.realpath(named)) if named.is_symlink() else named
    if target.is_symlink():  # realpath gave up: a loop
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(named))
    try:
        parent = os.stat(target.parent)
    except OSError as e:  # ENOENT, ENOTDIR, ELOOP: what opening `named` would say
        raise type(e)(e.errno, e.strerror, str(named)) from None
    if not stat.S_ISDIR(parent.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(named))
    try:
        st: os.stat_result | None = os.stat(target)
    except FileNotFoundError:
        st = None
    if st is not None and (
        st.st_nlink > 1 or st.st_uid != os.geteuid() or not os.access(target, os.W_OK)
    ):
        _write_in_place(target, text, fsync=fsync)
        return
    try:
        atomic_write(target, text, fsync=fsync)
    except OSError as e:
        if st is None or e.errno not in (errno.EACCES, errno.EPERM, errno.EBUSY):
            raise
        _write_in_place(target, text, fsync=fsync)


def _write_in_place(path: Path, text: str, *, fsync: bool) -> None:
    """What `path.write_text(text, "utf-8")` does (truncate, write: same inode, owner,
    links), plus the flush to disk `fsync` asks for."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())


def _link_exclusive(
    tmp: Path, path: Path, raw: bytes, want: int | None, *, fsync: bool = True
) -> None:
    """Give the finished `tmp` the name `path` only if nothing has it: a hard link fails
    with `FileExistsError` when it does, so the check and the write are one step.

    A filesystem without hard links (FAT, many SMB and FUSE mounts) refuses the link
    itself, with whatever error it chooses (EPERM, ENOTSUP, or none at all); on ANY such
    refusal the file is created with `O_EXCL` -- still never replacing one -- and written
    in place, which is exclusive though no longer atomic for a reader. A real problem
    (a full disk, no permission) fails that create too, and is raised from it."""
    try:
        os.link(tmp, path)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if want is not None else 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        if want is not None:
            os.chmod(path, want)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def lock_path_for(path: Path | str) -> Path:
    """The lock file that guards `path`: `.<name>.lock` beside it."""
    path = Path(path)
    return path.with_name(f".{path.name}.lock")


@contextlib.contextmanager
def file_lock(
    lock: Path | str,
    timeout_s: float | None = None,
    *,
    poll_s: float = LOCK_POLL_S,
    create: bool = True,
) -> Iterator[None]:
    """Hold an exclusive `flock` on the file `lock` (created if missing) for the block.
    ``create=False`` locks only a file that is there (`FileNotFoundError` otherwise, nothing
    made): a probe of someone else's lock file must not bring a removed one back.

    `timeout_s=None` waits as long as it takes; a number gives up with `LockTimeout` when
    someone else still holds it after that many seconds (0: one try), retrying every
    `poll_s` until then. The lock is released
    when the block ends, by an exception too, and with the process if it dies.
    """
    lock = Path(lock)
    if create:
        lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, (os.O_CREAT if create else 0) | os.O_RDWR, 0o644)
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
                    time.sleep(poll_s)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# -- scratch space ----------------------------------------------------------------------


@contextlib.contextmanager
def scratch_dir(
    prefix: str | None = None, *, ignore_cleanup_errors: bool = False
) -> Iterator[Path]:
    """A temporary directory, removed (with what is in it) when the block ends."""
    with tempfile.TemporaryDirectory(
        prefix=prefix, ignore_cleanup_errors=ignore_cleanup_errors
    ) as d:
        yield Path(d)


@contextlib.contextmanager
def scratch_file() -> Iterator[Any]:
    """An anonymous temporary file open for binary reading and writing, gone when the
    block ends."""
    with tempfile.TemporaryFile() as fh:
        yield fh


@contextlib.contextmanager
def temp_text(text: str, suffix: str = "") -> Iterator[Path]:
    """A temporary file holding ``text``, for a program that wants a path; unlinked when
    the block ends, by an exception too."""
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as fh:
        fh.write(text)
        path = Path(fh.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def temp_root() -> Path:
    """The system temporary directory, for a fixed-name file shared between processes."""
    return Path(tempfile.gettempdir())


def unused_dir(prefix: str, under: Path | None = None) -> Path:
    """A path in ``under`` (the system temp directory by default) that is free now: its
    name was reserved and the directory removed again, for a tool that wants to create
    it itself (`git worktree add`)."""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=under))
    path.rmdir()
    return path


# -- paths ------------------------------------------------------------------------------


def ensure_ignored_dir(d: Path | str, *, comment: str = "", mode: int | None = None) -> Path:
    """Create the directory `d` (and its parents) with its own `.gitignore` of `*`, and
    return it.

    A directory of machine-local state ignores ITSELF rather than trusting the project's
    ignore file: a project whose `.ddflow/.gitignore` predates the directory, or that never
    ran `init`, must still not commit it. `comment` becomes a `# ...` first line; `mode`
    the file's permission bits (default: what `write_text` gives a new file). An existing
    `.gitignore` is never touched, and the create is exclusive, so concurrent callers
    leave one whole file. Raises OSError when it cannot be done; a caller to whom the
    ignore file is a convenience suppresses that itself.
    """
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        text = (f"# {comment}\n" if comment else "") + "*\n"
        with contextlib.suppress(FileExistsError):
            atomic_write(ignore, text, mode=mode, exclusive=True)
    return d
