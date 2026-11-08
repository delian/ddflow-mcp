"""The scratch worktree the CI checks run in, and the merge that builds it.

Split out of ``ddflow/services/ci.py``: the owner marker that claims a scratch tree, the
signal-time cleanup, and ``merge_tree`` -- the throwaway worktree of ``ref`` with ``base``
merged in, removed on exit.
"""

from __future__ import annotations

import os
import shutil
import signal
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ...infra import fsio
from ...infra import worktree as W
from .. import ci_conflict as CC
from .types import CANNOT_MERGE, CONFLICT, NOT_MERGEABLE, Failure

#: The scratch trees of a ci run (`merge_tree`), made under the system temp directory: the
#: prefix `cleanup` recognises when it looks for the leftovers of killed runs (B43593d1f3c).
SCRATCH_PREFIX = "ddflow-ci."
#: The suffix of the file that claims a scratch tree for the process that owns it while a ci
#: run lives: ``<tree><OWNER_SUFFIX>`` holds ``pid start-ticks``, so `cleanup` can tell a live
#: run's tree from a killed run's leftover (keyed by pid AND start time: a reused pid is not
#: the owner that died).
OWNER_SUFFIX = ".owner"


def owner_line() -> str:
    """This process's owner-marker line: ``pid start-ticks``. Keyed by pid AND start time
    (like the harness identity), so a reused pid never reads as the owner that died. On a
    host with no /proc the line is the bare pid; `cleanup` treats a missing start-ticks as
    unknown (LEFT ALONE, never pruned), so the fallback trades liveness for never guessing."""
    pid = os.getpid()
    try:
        fields = Path(f"/proc/{pid}/stat").read_text("utf-8", "replace").rsplit(")", 1)[1].split()
        return f"{pid} {fields[19]}"
    except (OSError, IndexError):
        return str(pid)


def owner_marker(tree: Path) -> Path:
    """The sibling file that claims scratch tree ``tree`` for its owner: ``<tree>[OWNER_SUFFIX]``.

    Outside the tree, so the checked-out files stay untouched by it."""
    return Path(f"{tree}{OWNER_SUFFIX}")


def _claim_owner(tree: Path) -> Path:
    """Write the owner marker for ``tree``, and return it. A marker that cannot be written
    (a read-only temp dir) costs `cleanup` the liveness check, never the run itself."""
    marker = owner_marker(tree)
    try:
        fsio.atomic_write(marker, owner_line() + "\n")
    except OSError:
        pass
    return marker


def _remove_scratch(repo: Path, tree: Path) -> None:
    """Remove scratch tree ``tree`` and prune its git registration: the exit path of the
    infra scratch helper, factored for a SIGNAL, under which no ``finally`` can run and
    this handler is the only cleanup there is. Never raises: a removal that fails leaves
    the tree for `cleanup` to name and prune."""
    W.git(repo, "worktree", "remove", "--force", str(tree))
    shutil.rmtree(tree, ignore_errors=True)
    W.git(repo, "worktree", "prune")


@contextmanager
def _remove_on_signals(repo: Path, tree: Path, marker: Path) -> Iterator[None]:
    """Remove ``tree`` even when a signal ends the run: SIGTERM (a supervisor), SIGHUP (a
    closed session) and SIGINT (ctrl-C) first free tree, marker and registration, then
    re-raise themselves with their default action, so their default kill, or a caller's
    handler, never outlives the cleanup. The handlers this installs are restored (and the
    marker removed) however the body ends; a non-main thread cannot install handlers, and
    keeps whatever it arrived with."""
    previous: dict[signal.Signals, object] = {}

    def free_then_die(signum: int, _frame) -> None:
        try:
            _remove_scratch(repo, tree)
            marker.unlink(missing_ok=True)  # the process dies here: no `finally` follows
        except OSError:
            pass
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, free_then_die)
        except (ValueError, OSError):
            pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def merge_tree(
    repo: Path,
    ref: str,
    base: str,
    pins: Callable[[], Iterable[str]] | None = None,
    deadline_s: float | None = None,
) -> Iterator[tuple[Path | None, str]]:
    """A scratch worktree of `ref` with `base` merged in: (path, "") or (None, why not).

    The "why" is a ``Failure`` (a str) whose ``kind`` is "ok", "conflict" or "cannot-merge".

    Removed on exit. A base already contained in `ref` needs no merge; a base that
    conflicts with `ref` is reported as the failure it is. ``deadline_s`` bounds the git
    that makes the tree and the merge inside it: past it git is killed and reported as a
    cannot-merge failure, and the throwaway tree is removed (the probe's old-git fallback
    passes its ``[locks].probe_timeout_s`` so the CLI never outlives it, LK-probe-scratch-deadline)."""
    with W.scratch_tree(
        repo, ref, detach=True, prefix=SCRATCH_PREFIX, quiet=True, claim=_claim_owner,
        deadline_s=deadline_s,
    ) as s:
        if s.path is None:
            yield (
                None,
                Failure(f"could not create a scratch worktree of {ref}: {s.error}", CANNOT_MERGE),
            )
            return
        tree = s.path
        marker = owner_marker(tree)  # written by `claim`, BEFORE the add registered the tree
        deadline = {} if deadline_s is None else {"timeout": deadline_s}
        with _remove_on_signals(repo, tree, marker):
            if (
                base
                and not W.git(
                    repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", **deadline
                ).ok
            ):
                yield (
                    None,
                    Failure(
                        f"could not merge {base} into {ref}: {base!r} is not a commit",
                        CANNOT_MERGE,
                    ),
                )
                return
            if base and W.git(repo, "merge-base", "--is-ancestor", base, ref, **deadline).code != 0:
                # No hook of the repository runs in the scratch merge (core.hooksPath): the
                # post-merge hook regenerated pins by running the golden suite, which outlived
                # the git timeout and was reported as a conflict (B36c484af97).
                m = W.git(
                    tree,
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "user.name=ddflow",
                    "-c",
                    "user.email=ddflow@localhost",
                    "merge",
                    "--no-edit",
                    "--no-ff",
                    base,
                    **deadline,
                )
                said = m.out or m.err
                # A conflict is git's own answer: exit 1, or histories with nothing in common.
                # Anything else (killed at the timeout, a base it cannot resolve, a lock held:
                # exit 128) is a merge it could not do, which the gate cannot call a conflict.
                conflict = m.code == 1 or "unrelated histories" in said
                if not m.ok and not conflict:
                    yield (
                        None,
                        Failure(
                            f"could not merge {base} into {ref}: {m.err or m.out}"[-400:],
                            CANNOT_MERGE,
                        ),
                    )
                    return
                if not m.ok:
                    yield (
                        None,
                        Failure(
                            f"{ref} {NOT_MERGEABLE} {base}: {(m.out or m.err)[-400:]}"
                            + CC.cause(tree, base, pins),
                            CONFLICT,
                        ),
                    )
                    return
            yield tree, Failure("", "ok")
