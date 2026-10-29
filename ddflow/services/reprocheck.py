"""Run a bug's ``--repro-cmd`` at filing, in a scratch checkout of the recorded sha.

The reproduction bundle stores the command stamped "given, not executed" (the bundle
task); this module is the execution the verify item adds: `check` runs the command in a
throwaway checkout of the sha recorded with the bug (`infra.worktree.scratch_tree`, the
same machinery the regression verify reuses), the caller decides how many runs the
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
the verdict. The line runs THROUGH THE SHELL, like the ci gate's line: it is the
operator's own command, so ``VAR=val cmd`` prefixes, ``&&``, redirections and globs are
the shell's, and a timeout kills the whole process group (`infra.proc.run_shell`).
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..infra import proc as P
from ..infra import worktree as W


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
    the SHELL's: `run_shell` runs it whole, so a `VAR=val` prefix, ``&&``, a redirection
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
    tails = Path(tempfile.mkdtemp(prefix="ddflow-repro-"))
    try:
        with W.scratch_tree(repo, sha, detach=True, prefix="ddflow-repro-", quiet=True) as s:
            if s.path is None:
                return {
                    "unavailable": f"no scratch checkout of {sha[:12]}: {s.error}",
                    "load": load_start,
                }
            # A checkout whose .git/config cannot be read as a regular file (a linked
            # worktree's .git is a FILE) is no scratch we can prime: unavailable, never
            # a raise out of a filing (the finally below removes the tails dir).
            try:
                pristine_cfg = (s.path / ".git" / "config").read_bytes()
            except OSError:
                return {
                    "unavailable": "the scratch checkout's .git/config is not readable",
                    "load": load_start,
                }
            codes: list[int] = []
            out: list[bytes] = []
            bigger = False
            unreached = 0
            # The first-run's .git/config snapshot: reset --hard + clean -xdfq wipe the
            # WORKING tree, not the config a run wrote into .git/config -- restoring the
            # snapshot brings that back too, so later runs see the checkout the first did.
            pristine_cfg = (s.path / ".git" / "config").read_bytes()
            for _ in range(count):
                if not _prime_scratch(s.path, sha, pristine_cfg):
                    unreached += 1
                    continue
                # One unpredictable name per run: a poisoned run that mkdir'd the NEXT
                # run's path cannot know the name (it was never on any argv line).
                code, tailed, size = _one_run(
                    s.path, tails / secrets.token_hex(6), line, keep, timeout_s
                )
                if code is None:
                    unreached += 1
                    continue
                codes.append(code)
                if tailed:
                    out.append(tailed)
                bigger = bigger or size > keep
    finally:
        shutil.rmtree(tails, ignore_errors=True)
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


def _prime_scratch(scratch: Path, sha: str, pristine_cfg: bytes) -> bool:
    """Reset the scratch checkout to ``sha`` BEFORE one run; False when the reset could
    not be guaranteed (with no fresh checkout there is no run to read).

    A stale lock is dropped and the pristine config written FIRST: a run that poisoned
    the config or left a lock must not corrupt the reset itself. os.replace swaps the
    NODE itself -- a symlink left at .git/config is replaced with the pristine regular
    file, its target never written -- and the STAGING name is unpredictable and opened
    O_NOFOLLOW|O_EXCL, so a planted symlink or FIFO at a guessed staging path can
    neither redirect nor block the write. An untracked directory squatting a tracked
    path can fail a reset: cleaning first (and once more after a failed reset) lets the
    reset land on the tree that the clean freed. Any filesystem surprise the line left
    degrades to False -- the run is unreached, the filing never crashes on it."""
    try:
        # The git dir is real before ANY path under it is trusted: a symlink planted
        # there would reroute the pristine write and the resets onto the operator's tree.
        git_dir = scratch / ".git"
        if git_dir.is_symlink():
            return False
        for lock in ("index.lock", "HEAD.lock"):
            (git_dir / lock).unlink(missing_ok=True)
        cfg_path = git_dir / "config"
        for _ in range(4):
            staged = git_dir / f".pristine-{secrets.token_hex(6)}"
            try:
                fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as fh:
                fh.write(pristine_cfg)
            break
        else:
            return False
        os.replace(staged, cfg_path)
        cleaned = P.capture(["git", "clean", "-xdfq"], cwd=scratch, timeout=60.0)
        reset = P.capture(["git", "reset", "--hard", sha], cwd=scratch, timeout=60.0)
        if reset.code != 0:
            cleaned = P.capture(["git", "clean", "-xdfq"], cwd=scratch, timeout=60.0)
            reset = P.capture(["git", "reset", "--hard", sha], cwd=scratch, timeout=60.0)
        return reset.code == 0 and cleaned.code == 0
    except OSError:
        return False


def _one_run(
    scratch: Path, tail_file: Path, line: str, keep: int, timeout_s: float
) -> tuple[int | None, bytes, int]:
    """One run of ``line`` in the scratch tree: its exit code (None when it reached none),
    the tail bytes of its output file, and that file's size. The run is a subshell with
    the WHOLE line redirecting to ``tail_file`` (outside the checkout; /dev/null when
    nothing is kept), so every command the line runs writes there and nowhere else."""
    dest = f" > {shlex.quote(str(tail_file))} 2>&1" if keep else " > /dev/null 2>&1"
    shot = P.run_shell(f"( {line}\n){dest}", timeout_s=timeout_s, cwd=scratch, merge_stderr=True)
    if shot.could_not_run or shot.timed_out or shot.code is None:
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
