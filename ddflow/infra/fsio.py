"""The file layer: one way to replace a file atomically, one way to lock one, and the path
helpers every writer shares (a directory that ignores itself, a repo-relative name, a
marked region of lines).

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
import errno
import fcntl
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
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
    lock: Path | str, timeout_s: float | None = None, *, poll_s: float = LOCK_POLL_S
) -> Iterator[None]:
    """Hold an exclusive `flock` on the file `lock` (created if missing) for the block.

    `timeout_s=None` waits as long as it takes; a number gives up with `LockTimeout` when
    someone else still holds it after that many seconds (0: one try), retrying every
    `poll_s` until then. The lock is released
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
                    time.sleep(poll_s)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def repo_rel(
    repo: Path | str, path: Path | str, *, as_given: bool = False, strict: bool = True
) -> str | None:
    """`path` relative to `repo` as a POSIX string. Outside the repository: None when
    `strict` (the default), else `path` itself, as given, unchanged.

    By default both sides are resolved, so a repo reached through a symlink and a path
    that was resolved (or the other way round) still compare equal, and "outside" means
    the real file is outside. `worktree.store_path`, `enforce._rel` and the pins report
    make their "inside the repository, and as what" decision here.

    `as_given=True` is for REPORTS: "inside" then also means "named inside". It first
    tries `path` exactly as written, against `repo` as written and as resolved, and
    resolves only when neither holds it, so a file the operator listed (a symlinked
    suite or document, or one under a symlinked directory, even one pointing out of the
    repo) prints by that name, not wherever the link points. A `path` containing `..`
    is never taken as written, since `..` can leave the repository: it is resolved.
    """
    root = Path(repo).resolve()
    if as_given:
        p = Path(path)
        for base in (Path(repo), root):
            # `..` is not collapsed lexically: `repo/../x` lies outside, so resolve it
            if p.is_relative_to(base) and ".." not in p.relative_to(base).parts:
                return p.relative_to(base).as_posix()
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None if strict else str(path)


class RegionError(ValueError):
    """A region's markers are duplicated, unbalanced or reversed: which lines to replace
    is ambiguous, and guessing would destroy text. Only a person can repair it."""


@dataclass(frozen=True, slots=True)
class Region:
    """The lines of a text from a line matching `begin` through a line matching `end`.

    The one line-splice primitive under every managed block: a marker is a WHOLE line
    (`re.fullmatch` per line, a trailing `\\r` ignored), so a marker quoted inside prose
    is never one. The marker grammar is the caller's (D-doc-regions); this only finds
    and replaces. `Region(None, None)` -- `APPEND` -- has no markers: it is never found,
    and splicing it appends.
    """

    begin: str | None
    end: str | None

    def find(self, text: str) -> tuple[int, int, int, int] | None:
        """`(start, body_start, body_end, stop)` offsets: the begin line starts at `start`,
        the body runs from `body_start` to `body_end` (the start of the end line), and the
        end line, with its newline, stops at `stop`. None when neither marker is present;
        RegionError when the markers are broken."""
        if self.begin is None or self.end is None:
            return None
        b, e = re.compile(self.begin), re.compile(self.end)
        begins: list[tuple[int, int]] = []
        ends: list[tuple[int, int]] = []
        at = 0
        for line in text.splitlines(keepends=True):
            bare = line.rstrip("\n").removesuffix("\r")
            is_end = e.fullmatch(bare) is not None
            # An open region's closing line is its end even when it also matches `begin`:
            # one marker line can open and close (`---` around front matter).
            if is_end and len(begins) > len(ends):
                ends.append((at, at + len(line)))
            elif b.fullmatch(bare):
                begins.append((at, at + len(line)))
            elif is_end:
                ends.append((at, at + len(line)))
            at += len(line)
        if not begins and not ends:
            return None
        if len(begins) != 1 or len(ends) != 1 or begins[0][0] > ends[0][0]:
            problem = "duplicate" if len(begins) > 1 or len(ends) > 1 else "unbalanced or reversed"
            raise RegionError(f"{problem} region markers ({self.begin!r} ... {self.end!r})")
        return begins[0][0], begins[0][1], ends[0][0], ends[0][1]

    def body(self, text: str) -> str | None:
        """The text between the marker lines, or None when the region is absent."""
        at = self.find(text)
        return None if at is None else text[at[1] : at[2]]

    def splice(self, text: str, block: str, *, gap: str = "\n") -> str:
        """`text` with the region's lines (markers included) replaced by `block`, the rest
        byte for byte. When the region is absent, `block` is appended: `text` less its
        trailing whitespace, then `gap`, then `block`. `block` is used as given -- it
        carries its own markers and final newline."""
        at = self.find(text)
        if at is None:
            return text.rstrip() + gap + block
        return text[: at[0]] + block + text[at[3] :]


#: The markerless region: splicing it appends.
APPEND = Region(None, None)
