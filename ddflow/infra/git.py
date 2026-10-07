"""The one place git is run (D-unify: one process layer).

``run(repo, *args)`` is the single way to start ``git -C <repo> ...``: stdin detached
(through `proc.run`), a default timeout, ``LC_ALL=C`` so the messages callers match on do
not depend on the operator's locale, and a result that is NEVER an exception for "git
could not run": a missing git or a timeout is a `GitResult` whose ``unavailable`` is true,
so a caller cannot mistake "could not tell" for "nothing there" without writing the test
that says so.

Three shapes of output, one function:

- text (the default): ``out`` and ``err`` stripped, decoded with ``errors``;
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
GIT_TIMEOUT = 300
#: Seconds a path listing (`git_paths`) may run.
LISTING_TIMEOUT = 60

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
    #: Raw stdout, set by ``binary=True`` / ``z=True`` (``out`` is then its lossy decoding).
    out_bytes: bytes = b""
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
        if not self.ok:
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
    errors: str = "strict",
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
            out_bytes=exc.stdout if isinstance(exc.stdout, bytes) else b"",
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
            out_bytes=out if raw else b"",
        )
    if check and not res.ok:
        raise GitError(f"git {' '.join(args)} failed ({res.code}): {res.err or res.out}")
    return res


def git_paths(
    repo: Path | str, *args: str, timeout: float | None = LISTING_TIMEOUT
) -> list[str] | None:
    """File paths git lists, EXACTLY as the filesystem names them; None if git failed.

    The way a path listing SHOULD be read -- not yet the only one: several older listings
    (`dirty`, the gate tree fingerprint, and others named in docs/HANDOFF.md §2) still
    read without `-z` and are not migrated. A caller must treat None as "could not
    tell", never as "no paths". Adds `-z` and reads BYTES:
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
