"""Run a bug's ``--repro-cmd`` at filing, in a scratch checkout of the recorded sha.

The reproduction bundle stores the command stamped "given, not executed" (the bundle
task); this module is the execution the verify item adds: `check` runs the command in a
throwaway checkout of the sha recorded with the bug (a full standalone clone -- see
`_isolated_clone` -- not a linked worktree), the caller decides how many runs the
reading needs (`[bugs] repro_runs`), and the record the bug stores says only what the
runs answered:

    "executed": true
    "at": the sha the checkout ran,
    "reproduced": "yes" when at least one run exited non-zero,
    "runs": how many runs reached an exit code,
    "failures": how many of those exited non-zero,
    "load": the one-minute load when the window opened,
    "output": the tail of the runs' merged output (`[bugs] repro_tail_bytes`),
    "unavailable": why nothing reached an exit code.

A bug whose repro did not fail is UNCONFIRMED until a second sighting or a failing run
(the fold keeps "yes" once a run places it). Nothing is read as reproduced without a run
behind it: a window that produced no exit code is unavailable, never reproduced, and a
run that never reached an exit code is counted apart as ``unreached``, never cited for
the verdict. The line runs THROUGH THE SHELL, like the ci gate's line: it is the CLI
operator's own ``--repro-cmd``, so ``VAR=val cmd`` prefixes, ``&&``, redirections and
globs are the shell's, and a timeout kills the whole process group
(`services.cmdrunner.CommandRunner`). Agent-supplied ``evidence`` reaches the record as
the bug's ``evidence`` text instead and is STORED, never executed here.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..infra import fsio as F
from ..infra import git as G
from . import cmdrunner as CR


def check(
    repo: Path, sha: str, cmd: str, runs: int, timeout_s: float, tail_bytes: int
) -> dict[str, Any]:
    """The record the caller stores for ``cmd`` executed at ``sha`` up to ``runs`` times.

    ``runs`` below 1 runs it once; all runs happen in ONE scratch checkout of ``sha``,
    so the command sees the recorded commit, not the filer's uncommitted edits; no
    ``sha`` (git could not name one) is ``unavailable``, never a run somewhere else."""
    count = max(1, int(runs))
    if not (cmd or "").strip():
        return {"unavailable": "repro command is empty"}
    clean = str(sha or "").strip()
    if not clean:
        return {"unavailable": "the tree sha the repro would run at is not recorded"}
    return _run_at(repo, clean, cmd, count, float(timeout_s), int(tail_bytes))


def _run_at(
    repo: Path, sha: str, line: str, count: int, timeout_s: float, tail_bytes: int
) -> dict[str, Any]:
    """Run ``line`` up to ``count`` times in one scratch checkout of ``sha``. The line is
    the SHELL's: `CommandRunner` runs it whole, so a `VAR=val` prefix, ``&&``, a redirection
    or a glob behave as the saw it did, and a timeout kills the line's process group. A
    run cut off (timed out, could not start) never yields an exit code: it is not part of
    the verdict, it is counted as ``unreached`` -- without runs that answered, the record
    is ``unavailable``, never reproduced. The line runs wrapped in a subshell whose WHOLE
    output is redirected to a tail file (``( line\n) > file 2>&1``): compound lines the
    shell supports -- ``a && b``, ``;``, a trailing comment -- all write there, none of
    it reaches this process's buffer. The tail file lives in a throwaway dir OUTSIDE the
    checkout, so the file itself never changes the tree the command is reproducing, and
    only its tail bytes are read back. The joined tails' byte-cut tail is the record's
    single ``output``."""
    load_start = _first_load()
    keep = max(0, int(tail_bytes))
    with F.scratch_dir(prefix="ddflow-repro-", ignore_cleanup_errors=True) as tails:
        codes: list[int] = []
        out: list[bytes] = []
        bigger = False
        unreached = 0
        for nth in range(count):
            # A fresh clone PER RUN, at an unpredictable name per run: a poisoned run that
            # mkdir'd the NEXT clone's path cannot know the name (it was never on any argv
            # line, and this run's own clone is removed before the next is made). A stateful
            # line can mutate its clone's config and tree all it likes and no later run
            # inherits it, and a run that rewired ``.git`` (a symlink, ``core.worktree``
            # pointing elsewhere) cannot steer any later run's reset onto another checkout:
            # there is no reset, each run starts from a fresh throwaway clone.
            with _isolated_clone(repo, sha, tails / f"run-{nth}-{secrets.token_hex(6)}") as clone:
                if clone is None:
                    unreached += 1
                    continue
                code, tailed, size = _one_run(
                    clone, tails / secrets.token_hex(6), line, keep, timeout_s
                )
            if code is None:
                unreached += 1
                continue
            codes.append(code)
            if tailed:
                out.append(tailed)
            bigger = bigger or size > keep
        if not codes:
            why = "every run was cut off before an exit code" if unreached else "no run"
            record: dict[str, Any] = {
                "executed": False,
                "unavailable": f"{why} reached an exit code to read",
            }
            record["unreached"] = unreached
            record["load"] = load_start
            return record
    failed = [c for c in codes if c != 0]
    record = {
        "executed": True,
        "at": sha,
        "reproduced": "yes" if failed else "no",
        "runs": len(codes),
        "failures": len(failed),
        "load": load_start,
    }
    if unreached:
        record["unreached"] = unreached
    joined = b"\n".join(out)
    if joined and keep:
        # The whole pipeline is BYTES until now: decode-replace inflation can never say a
        # store-fits cut of one byte is 'truncated', and the join's byte length against
        # the budget IS the truncation (a run file larger than the budget staffed the cut).
        record["output_truncated"] = bigger or len(joined) > keep
        # The budget staffs the RAW bytes the tail keeps (joined[-keep:] is never longer
        # than it); invalid bytes render as the replacement marker, whose 3-byte glyph is
        # a rendering of the kept byte, not extra bytes stored.
        record["output"] = joined[-keep:].decode("utf-8", errors="replace")
    return record


@contextlib.contextmanager
def _isolated_clone(repo: Path, sha: str, dst: Path) -> Iterator[Path | None]:
    """A throwaway FULL clone of ``repo`` checked out at ``sha``, in its own directory under
    the throwaway tails dir; None when the clone could not be made.

    A full clone -- not `git worktree add` -- is the isolation the repro needs (bug
    Bc771f31635): a linked worktree's ``.git`` is a gitdir POINTER into the primary's own
    metadata, so a repro command that runs `ddflow` from inside it (`ddflow cadence
    --ran NOPE`) resolves the primary checkout and appends its events to the REAL committed
    ``.ddflow/events``. A clone's ``.git`` is a directory in the throwaway, so the child's
    repo root is the clone itself, its events land in the clone's own ``.ddflow/events``,
    and the whole directory is removed whatever happened. ``--local --no-hardlinks`` copies
    objects, so the clone never dangles on the operator's pack being GC'd mid-run, and every
    remote the clone recorded is dropped (by its own name): the throwaway has no business
    reaching the operator's repository, and a repro command reading the remotes learns no
    path back to it."""
    clone = G.run(
        repo,
        "clone",
        "--local",
        "--no-hardlinks",
        "--no-checkout",
        "--quiet",
        str(repo),
        str(dst),
        timeout=180.0,
    )
    if not clone.ok:
        yield None
        return
    _drop_remotes(dst)
    out = G.run(dst, "checkout", "--detach", sha, timeout=120.0)
    if not out.ok:
        F.rmtree(dst)
        yield None
        return
    try:
        yield dst
    finally:
        F.rmtree(dst)


def _drop_remotes(dst: Path) -> None:
    """Remove every remote the clone's config recorded, by each remote's OWN name (never the
    assumed name ``origin``: `clone.defaultRemoteName` renames it, and a URL left behind is a
    path a repro command CAN read back to the operator repo). The throwaway has no business
    reaching the operator's repository; a clone already copied its objects, so no remote is
    needed to check the recorded commit out."""
    listed = G.run(dst, "remote", timeout=30.0)
    for name in (listed.out or "").splitlines():
        if name.strip():
            G.run(dst, "remote", "remove", name.strip(), timeout=30.0)


def _one_run(
    scratch: Path, tail_file: Path, line: str, keep: int, timeout_s: float
) -> tuple[int | None, bytes, int]:
    """One run of ``line`` in the scratch tree: its exit code (None when it reached none),
    the tail bytes of its output file, and that file's size. The run is a subshell with
    the WHOLE line redirecting to ``tail_file`` (outside the checkout; /dev/null when
    nothing is kept), so every command the line runs writes there and nowhere else."""
    dest = f" > {shlex.quote(str(tail_file))} 2>&1" if keep else " > /dev/null 2>&1"
    shot = CR.CommandRunner().run(
        CR.Declared(f"( {line}\n){dest}", "bug --repro-cmd"),
        cwd=scratch,
        timeout_s=timeout_s,
        merge_stderr=True,
    )
    if shot.status != "ran" or shot.code is None:
        return None, b"", 0
    code = int(shot.code)
    if code in (126, 127):
        # POSIX's could-not-execute exits: a missing or unexecutable program made the
        # SHELL answer before the line's program ever ran. That confirms nothing.
        return None, b"", 0
    if not keep:
        return code, b"", 0
    tailed, size = _tail_from_file(tail_file, keep)
    if tailed is None:
        # The tail file was never created: the shell's own redirect failed (a removed or
        # full tails dir) and the LINE never ran. That is no run at all -- counting its
        # exit would confirm a bug from a command that never executed.
        return None, b"", 0
    return code, tailed, size


def _tail_from_file(path: Path, keep: int) -> tuple[bytes | None, int]:
    """The last ``keep`` BYTES of ``path`` and its total size; ``(None, 0)`` when the file
    is NOT THERE (the shell could not create it -- the run never happened), ``(b"", size)``
    when it is not readable otherwise. Reading with a seek bounds what an enormous run
    loads into memory; the size still says a run was cut. Bytes stay bytes to ``_run_at``:
    decode-replace inflation happens once, at presentation, and cannot wander into the
    arithmetic. The open is O_NOFOLLOW|O_NONBLOCK: a repro line that planted a symlink (a
    leak) or a FIFO (a hang) at the tail path -- it knows the path, it is part of its own
    argv -- is refused by O_NOFOLLOW and fails O_NONBLOCK with ENXIO instead of blocking."""
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd = os.open(path, flags)
    except OSError:
        return None, 0
    try:
        with os.fdopen(fd, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - keep))
            return fh.read(keep), size
    except OSError:
        return b"", 0


def _first_load() -> str:
    """The load the conditions record wants: the one-minute number, 'unknown' when not had."""
    try:
        return f"{os.getloadavg()[0]:.2f}"
    except (OSError, AttributeError):
        return "unknown"
