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
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ddflow import FORMAT_LEVEL, __version__
from ddflow.core.digest import content_digest
from ddflow.core.events import is_older

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


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Why `read_json` could not give a JSON object; each caller words its own refusal.

    `kind`: "invalid" (not JSON), "unreadable" (an OSError, or bytes that are not UTF-8)
    or "not-object" (valid JSON, but not an object: `value` holds it). `detail` is the
    error's own text ("" for not-object)."""

    path: Path
    kind: Literal["invalid", "unreadable", "not-object"]
    detail: str = ""
    value: Any = None


def read_json(path: Path | str) -> dict[str, Any] | Unreadable:
    """A JSON config file another tool also writes (an MCP server list, harness settings)
    as a dict: a missing or empty file is `{}`, anything else that is not a JSON object is
    an `Unreadable` saying why -- never an exception, so no reader can let a
    `UnicodeDecodeError` or a list escape as a traceback where its neighbours refuse."""
    path = Path(path)
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return {}
    except (UnicodeDecodeError, OSError) as exc:
        return Unreadable(path, "unreadable", str(exc))
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        return Unreadable(path, "invalid", str(exc))
    if not isinstance(data, dict):
        return Unreadable(path, "not-object", value=data)
    return data


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


# --- Managed: the one "is this region ours, and who wrote it" interface -----------------

#: Hex characters of the body digest a managed header carries.
MANAGED_DIGEST_LEN = 12

_ATTR = re.compile(r"([A-Za-z0-9_-]+)=(\S+)")


#: A format level longer than this many digits is read as MAX_LEVEL: above any real level,
#: and never run through ``int`` (CPython refuses a very long digit string).
MAX_LEVEL_DIGITS = 9
MAX_LEVEL = 10**MAX_LEVEL_DIGITS


def parse_level(text: str) -> int | None:
    """The format level a ``fmt=`` value spells, or None when it is not a plain decimal
    number (an unknown attribute). An absurdly long number is a level above every real one."""
    if not (text.isascii() and text.isdecimal()):
        return None
    return int(text) if len(text) <= MAX_LEVEL_DIGITS else MAX_LEVEL


class NewerContent(RuntimeError):
    """A write would replace content a NEWER ddflow wrote (D-compat 2): the one refusal
    the contract allows. `needs` is the version to upgrade to (exit 3, "upgrade ddflow to
    >= X")."""

    def __init__(self, what: str, needs: str, fmt: int) -> None:
        super().__init__(
            f"{what} was written by ddflow {needs} (format level {fmt}); "
            f"upgrade ddflow to >= {needs} before rewriting it"
        )
        self.needs = needs
        self.fmt = fmt


def _digest(body: str) -> str:
    return content_digest(body, length=MANAGED_DIGEST_LEN)


@dataclass(frozen=True, slots=True)
class Stamp:
    """What a managed region's begin line says: the writer's version, its format level,
    the digest of the body it wrote, and every attribute this version does not know
    (kept, in order, when the region is rewritten at the same level)."""

    version: str
    fmt: int
    sha: str
    extra: tuple[tuple[str, str], ...] = ()


#: What `Managed.state` answers.
State = Literal["absent", "current", "older", "newer", "edited"]


@dataclass(frozen=True, slots=True)
class Managed:
    """One kind of managed region: `<open> ddflow:begin <name> ddflow=V fmt=N sha=H <close>`
    ... `<open> ddflow:end <name> <close>`, each a whole line (the D-doc-regions grammar,
    extended by version, format level and body digest).

    `owns` says whether a text holds the region, `stamp` what wrote it, `state` how it
    compares with this ddflow -- `newer` (a higher FORMAT LEVEL; a higher version alone never refuses), `older`, `edited`
    (the body no longer matches its digest, so a person changed it) or `current` -- and
    `splice` replaces it, refusing (NewerContent) only a write that would downgrade a
    newer region. The 3-way merge of an `edited` region is the caller's (D-doc-regions).
    """

    name: str
    open: str = "<!--"
    close: str = "-->"

    def _line(self, edge: str, attrs: str = "") -> str:
        tail = f" {attrs}" if attrs else ""
        return f"{self.open} ddflow:{edge} {self.name}{tail}" + (
            f" {self.close}" if self.close else ""
        )

    def _region(self) -> Region:
        o, c = re.escape(self.open), (rf" {re.escape(self.close)}" if self.close else "")
        n = re.escape(self.name)
        return Region(rf"{o} ddflow:begin {n}(?: [^\n]*?)?{c}", rf"{o} ddflow:end {n}{c}")

    def owns(self, text: str) -> bool:
        """True when the text holds this region. Raises RegionError for broken markers."""
        return self._region().find(text) is not None

    def stamp(self, text: str) -> Stamp | None:
        """The begin line's stamp, or None when the region is absent or carries none (a
        region written before stamps existed)."""
        at = self._region().find(text)
        if at is None:
            return None
        line = text[at[0] : at[1]].strip().removeprefix(self.open).removesuffix(self.close)
        attrs = dict(_ATTR.findall(line))
        if not {"ddflow", "fmt", "sha"} <= attrs.keys():
            return None
        level = parse_level(attrs["fmt"])
        if level is None:
            return None
        known = {"ddflow", "fmt", "sha"}
        extra = tuple((k, v) for k, v in _ATTR.findall(line) if k not in known)
        return Stamp(attrs["ddflow"], level, attrs["sha"], extra)

    def version(self, text: str) -> str | None:
        s = self.stamp(text)
        return None if s is None else s.version

    def state(self, text: str, *, version: str | None = None, fmt: int | None = None) -> State:
        version, fmt = version or __version__, FORMAT_LEVEL if fmt is None else fmt
        region = self._region()
        at = region.find(text)
        if at is None:
            return "absent"
        s = self.stamp(text)
        if s is None:
            return "older"
        if s.fmt > fmt:
            return "newer"
        if s.sha != _digest(text[at[1] : at[2]]):
            return "edited"
        return "older" if s.fmt < fmt or is_older(s.version, version) else "current"

    def render(
        self,
        body: str,
        *,
        version: str | None = None,
        fmt: int | None = None,
        extra: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """The whole region -- begin line, body, end line -- stamped for this ddflow."""
        body = body if body.endswith("\n") or not body else body + "\n"
        attrs = [
            ("ddflow", version or __version__),
            ("fmt", str(FORMAT_LEVEL if fmt is None else fmt)),
            ("sha", _digest(body)),
            *extra,
        ]
        return (
            self._line("begin", " ".join(f"{k}={v}" for k, v in attrs))
            + "\n"
            + body
            + self._line("end")
            + "\n"
        )

    def remove(self, text: str) -> str:
        """`text` less the region (both marker lines included), the rest byte for byte;
        unchanged when it holds none. Raises RegionError for broken markers."""
        at = self._region().find(text)
        return text if at is None else text[: at[0]] + text[at[3] :]

    def splice(
        self,
        text: str,
        body: str,
        *,
        version: str | None = None,
        fmt: int | None = None,
        gap: str = "\n",
    ) -> str:
        """`text` with the region replaced by `body` stamped for this ddflow (appended when
        absent), everything outside it byte for byte. A region written at a newer FORMAT LEVEL is
        NOT rewritten: NewerContent. At the same level the attributes this version does
        not know are carried over."""
        state = self.state(text, version=version, fmt=fmt)
        s = self.stamp(text)
        if state == "newer" and s is not None:
            raise NewerContent(f"the {self.name} region", s.version, s.fmt)
        level = FORMAT_LEVEL if fmt is None else fmt
        keep = s.extra if s is not None and s.fmt == level else ()
        return self._region().splice(
            text, self.render(body, version=version, fmt=fmt, extra=keep), gap=gap
        )
