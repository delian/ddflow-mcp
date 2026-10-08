"""The one place git is run (D-unify: one process layer).

``run(repo, *args)`` is the single way to start ``git -C <repo> ...``: stdin detached
(through `proc.run`), a default timeout, ``LC_ALL=C`` so the messages callers match on do
not depend on the operator's locale, and a result that is NEVER an exception for "git
could not run": a missing git or a timeout is a `GitResult` whose ``unavailable`` is true,
so a caller cannot mistake "could not tell" for "nothing there" without writing the test
that says so.

Three shapes of output, one function:

- text (the default): ``out`` and ``err`` stripped, decoded with ``errors`` ("replace": a
  commit subject or a blob that is not UTF-8 must not raise out of a runner that promises
  not to; a caller that needs the exact bytes asks for ``binary``);
- ``binary=True``: ``out_bytes`` holds stdout untouched (a file's own trailing newline is
  its content; a diff piped to ``patch-id`` must not be re-encoded);
- ``z=True``: ``-z`` is inserted before a ``--`` pathspec and ``paths()`` reads the NUL
  separated names exactly as the filesystem spells them (`os.fsdecode`), which text mode
  cannot: git C-quotes a non-ASCII name without ``-z``, and decodes a non-UTF-8 name
  strictly with it (see `git_paths`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.outcome import FAIL
from . import proc as P

#: Seconds a git call may run unless the caller says otherwise.
GIT_TIMEOUT = P.TIMEOUTS["git"]
#: Seconds a quick probe of the environment may run (`rev-parse`, `config`, a trailer parse).
PROBE_TIMEOUT = P.TIMEOUTS["probe"]
#: Seconds a path listing (`git_paths`) may run.
LISTING_TIMEOUT = P.TIMEOUTS["git_listing"]

#: `GitResult.code` of a git that never produced an exit status (missing, or timed out).
UNAVAILABLE = -1


class GitError(RuntimeError):
    #: Read by `core.outcome.declared_exit`: a git failure is a failure, not a bug.
    exit_code = FAIL


@dataclass
class GitResult:
    code: int
    out: str
    err: str
    #: False when a precondition refused the merge before git tried to merge the branch
    #: (the target is missing, checked out elsewhere, or another merge is in progress): the
    #: item's own branch was not judged, so it is not a failed merge of that branch.
    attempted: bool = True
    #: Raw stdout, set by ``binary=True`` / ``z=True`` (``out`` is then its lossy decoding);
    #: None when the call did not ask for it, so `paths()` cannot read a text call as empty.
    out_bytes: bytes | None = None
    #: git did not run to an exit status: not installed, or killed at its timeout.
    unavailable: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0

    def text(self) -> str:
        return (self.out or self.err).strip()

    def paths(self) -> list[str] | None:
        """The NUL separated names in ``out_bytes`` as the filesystem spells them; None when
        git failed -- "could not tell", never "no paths"."""
        if not self.ok or self.out_bytes is None:
            return None
        return [os.fsdecode(x) for x in self.out_bytes.split(b"\0") if x]


def _decode(raw: bytes | str | None, errors: str) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors)


def run(
    repo: Path | str,
    *args: str,
    timeout: float | None = GIT_TIMEOUT,
    z: bool = False,
    binary: bool = False,
    input: bytes | str | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
    errors: str = "replace",
) -> GitResult:
    """``git -C repo *args`` as a `GitResult`; see the module docstring.

    ``z`` adds ``-z`` before a ``--`` in ``args`` (or at the end) and implies ``binary``.
    ``input`` is fed to stdin (bytes, or text encoded as UTF-8). With ``check`` a non-zero
    exit raises `GitError`; an unavailable git always counts as non-zero.
    """
    argv = list(args)
    if z:
        at = argv.index("--") if "--" in argv else len(argv)
        argv.insert(at, "-z")
    raw = binary or z
    stdin = input.encode() if isinstance(input, str) else input
    child_env = {**os.environ, **(env or {}), "LC_ALL": "C"}
    try:
        p = P.run(
            ["git", "-C", str(repo), *argv],
            capture_output=True,
            input=stdin,
            timeout=timeout,
            env=child_env,
        )
    except P.TimeoutExpired as exc:
        res = GitResult(
            UNAVAILABLE,
            "",
            f"git {' '.join(args)} timed out after {timeout}s",
            out_bytes=exc.stdout if raw and isinstance(exc.stdout, bytes) else None,
            unavailable=True,
            timed_out=True,
        )
    except OSError as exc:
        res = GitResult(UNAVAILABLE, "", f"git could not run: {exc}", unavailable=True)
    else:
        out = p.stdout or b""
        res = GitResult(
            p.returncode,
            _decode(out, "replace" if raw else errors).strip(),
            _decode(p.stderr, "replace" if raw else errors).strip(),
            out_bytes=out if raw else None,
        )
    if check and not res.ok:
        raise GitError(f"git {' '.join(args)} failed ({res.code}): {res.err or res.out}")
    return res


def paths(
    repo: Path | str, *args: str, timeout: float | None = LISTING_TIMEOUT
) -> list[str] | None:
    """File paths git lists, EXACTLY as the filesystem names them; None if git failed.

    The one way a path listing is read. A caller must treat None as "could not tell",
    never as "no paths". Adds `-z` and reads BYTES:
    - without `-z`, git C-quotes any non-ASCII name (`"caf\\303\\251.txt"`), so a
      path built from it names no file and matches no glob;
    - with `-z` but in text mode, the raw bytes are decoded strictly as UTF-8, so ONE
      non-UTF-8 name anywhere raised out of the caller -- every commit (the hook), and
      every `ddflow lesson add` (the inventory scan), failed; text mode also rewrote a
      `\\r` in a name to `\\n`.
    `os.fsdecode` (surrogateescape) round-trips any name. Both bugs were found by roborev
    (on 40950c9 and 4f54455), the second introduced by the fix for the first, and then
    found again in a third caller (on e8543f9) -- hence one helper rather than three.

    `args` are git's arguments up to (not including) `-z`; pass any pathspec after a
    `"--"` in ``args`` as usual -- `-z` is inserted before it.
    """
    return run(repo, *args, z=True, timeout=timeout).paths()


#: The name this helper had before the queries were named; imports of it keep working.
git_paths = paths


def files(
    repo: Path | str,
    kind: str = "all",
    *,
    exclude: tuple[str, ...] = (),
    pathspec: tuple[str, ...] = (),
    timeout: float | None = LISTING_TIMEOUT,
) -> list[str] | None:
    """The files of a work tree, ``-z`` exact; None when git could not list them.

    ``kind``: ``"tracked"`` (in the index), ``"untracked"`` (neither tracked nor ignored)
    or ``"all"`` (both). ``exclude`` are pathspecs such as `core.bookkeeping.STATE_EXCLUDE`
    and ``pathspec`` narrows the listing (``"."`` is the tree below ``repo``, the default
    whenever ``exclude`` is given).
    """
    if kind not in _FILE_KINDS:
        raise ValueError(f"unknown kind of file listing {kind!r}; expected one of {_FILE_KINDS}")
    args = ["ls-files", *_FILE_KINDS[kind]]
    if pathspec or exclude:
        args += ["--", *(pathspec or (".",)), *exclude]
    return paths(repo, *args, timeout=timeout)


_FILE_KINDS: dict[str, tuple[str, ...]] = {
    "tracked": ("--cached",),
    "untracked": ("--others", "--exclude-standard"),
    "all": ("--cached", "--others", "--exclude-standard"),
}

#: Both columns of `git status --porcelain` that mark a path as unmerged.
_UNMERGED_XY = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


@dataclass(frozen=True)
class StatusEntry:
    """One `git status --porcelain -z` record: two status columns and the path(s)."""

    xy: str
    path: str
    #: The source of a rename or copy; "" for every other entry.
    orig: str = ""

    @property
    def untracked(self) -> bool:
        return self.xy == "??"

    @property
    def ignored(self) -> bool:
        return self.xy == "!!"

    @property
    def unmerged(self) -> bool:
        return self.xy in _UNMERGED_XY

    @property
    def paths(self) -> tuple[str, ...]:
        """Every path the entry names: a rename is BOTH its old and its new path."""
        return (self.path, self.orig) if self.orig else (self.path,)

    def line(self) -> str:
        """The entry as a porcelain text line, ``XY path`` (``XY old -> new`` for a rename),
        with the names as the filesystem spells them rather than C-quoted."""
        return f"{self.xy} {self.orig} -> {self.path}" if self.orig else f"{self.xy} {self.path}"


def status_run(
    repo: Path | str,
    *pathspec: str,
    untracked: str = "normal",
    ignored: str = "",
    timeout: float | None = GIT_TIMEOUT,
) -> GitResult:
    """The `git status --porcelain -z` call itself, for a caller that needs git's own
    message when it fails (`parse_status` reads it; `status` does both).

    ``untracked`` is git's ``--untracked-files`` mode: ``"normal"`` (an untracked directory
    is one entry), ``"all"`` (every file) or ``"no"``. ``ignored`` is git's ``--ignored`` mode
    (``"traditional"``, ``"matching"`` or ``"no"``; "" leaves ignored files out, as git does).
    ``pathspec`` narrows the status.
    """
    argv = ["status", "--porcelain", f"--untracked-files={untracked}"]
    if ignored:
        argv.append(f"--ignored={ignored}")
    if pathspec:
        argv += ["--", *pathspec]
    return run(repo, *argv, z=True, timeout=timeout)


def parse_status(r: GitResult) -> list[StatusEntry] | None:
    """The entries of a `status_run` result, names as the filesystem spells them; None when
    git failed -- "could not tell", never "nothing changed"."""
    if not r.ok or r.out_bytes is None:
        return None
    records = [os.fsdecode(x) for x in r.out_bytes.split(b"\0")]
    if records and records[-1] == "":
        records.pop()
    out: list[StatusEntry] = []
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        xy, path = rec[:2], rec[3:]
        orig = ""
        if "R" in xy or "C" in xy:  # `-z` puts the source path in the NEXT record
            if i < len(records):
                orig = records[i]
            i += 1
        if path:
            out.append(StatusEntry(xy, path, orig))
    return out


def status(
    repo: Path | str,
    *pathspec: str,
    untracked: str = "normal",
    ignored: str = "",
    timeout: float | None = GIT_TIMEOUT,
) -> list[StatusEntry] | None:
    """`git status --porcelain -z` parsed (see `status_run`); None when git failed."""
    r = status_run(repo, *pathspec, untracked=untracked, ignored=ignored, timeout=timeout)
    return parse_status(r)


def unmerged(repo: Path | str, timeout: float | None = LISTING_TIMEOUT) -> list[str] | None:
    """The paths a merge left unmerged, ``-z`` exact; None when git could not list them."""
    return paths(repo, "diff", "--name-only", "--diff-filter=U", timeout=timeout)
