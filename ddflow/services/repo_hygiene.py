"""`ddflow doctor` notes for a repository whose own git state was polluted (B1d82c6b76f).

A process that ran `git config` or `git init` with the wrong working directory (a test, a
script) leaves marks in the REAL repository that no ddflow command causes and that nothing
else reports: a local identity that makes every commit and merge from every worktree carry
the same stray author, `core.bare = true` on a checkout that has a work tree (so a merge into
main advances the branch ref but never the primary's index and files), and a primary whose
index no longer matches its HEAD. Each note names the exact repair command. Notes, not
problems: the repository still works, and an operator may have set any of these on purpose.
Git unable to answer is silence here, never a claim (the checks are advisory).
"""

from __future__ import annotations

from pathlib import Path

from ..core.bookkeeping import STATE_DIR
from ..infra import git as G
from ..infra import paths as PA

#: Paths a merge from a worktree is allowed to leave staged in the primary: the event log
#: and the other ddflow bookkeeping commit and merge on their own schedule.
BOOKKEEPING_EXCLUDE = f":(exclude){STATE_DIR}"

#: Stale paths named in a note before the rest are counted.
SHOWN = 5


def _get(repo: Path, *args: str) -> str | None:
    """The value; "" when the key is unset (git exits 1); None when git could not answer."""
    r = G.run(repo, "config", *args, timeout=G.PROBE_TIMEOUT)
    if r.ok:
        return r.out.strip()
    return "" if r.code == 1 else None


def _global(repo: Path, key: str) -> str | None:
    return _get(repo, "--global", "--get", key)


def _local(repo: Path, key: str) -> str:
    return _get(repo, "--local", "--get", key) or ""


def _primary(repo: Path) -> Path | None:
    """The checkout that owns the shared `.git` of ``repo`` (itself, or the primary of a
    linked worktree); None when ``repo`` is not in a git checkout or the shared dir is bare."""
    common = PA.common_dir(repo)
    if common is None or common.name != ".git":
        return None
    return common.parent


def identity_notes(repo: Path) -> list[str]:
    out = []
    primary = _primary(repo) or repo
    for key in ("name", "email"):
        local = _local(repo, f"user.{key}")
        if not local:
            continue
        shared = _global(repo, f"user.{key}")
        if shared is None:
            continue  # git could not read the global config: no claim
        if local != shared:
            out.append(
                f"the repository config sets user.{key} = {local!r}"
                + (f" while your global identity is {shared!r}" if shared else " (no global one)")
                + ": every commit and merge from every worktree carries it. If nobody set "
                f"it on purpose (a test or script run in the wrong directory does), "
                f"`git -C {primary} config --local --unset user.{key}`."
            )
    return out


def bare_notes(repo: Path) -> list[str]:
    primary = _primary(repo)
    if primary is None or not (primary / ".git").is_dir():
        return []
    if _local(repo, "core.bare") != "true":
        return []
    return [
        f"core.bare is true in {primary}/.git/config but the directory has a work tree: "
        "`git status` there fails, `git worktree list` calls it (bare), and a merge into main "
        "advances the branch ref without updating the primary's index and files. "
        f"`git -C {primary} config --local core.bare false`."
    ]


def stale_primary_notes(repo: Path) -> list[str]:
    """Tracked code paths whose staged content in the primary differs from its HEAD."""
    primary = _primary(repo)
    if primary is None or (primary / ".git").is_dir() is False:
        return []
    if G.run(primary, "rev-parse", "-q", "--verify", "MERGE_HEAD", timeout=G.PROBE_TIMEOUT).ok:
        return []  # mid-merge is its own problem, worded by the caller
    r = G.run(
        primary,
        "diff",
        "--cached",
        "--name-only",
        "--",
        ".",
        BOOKKEEPING_EXCLUDE,
        timeout=G.PROBE_TIMEOUT,
    )
    names = [n for n in r.out.splitlines() if n] if r.ok else []
    if not names:
        return []
    shown = ", ".join(names[:SHOWN]) + (
        f" (+{len(names) - SHOWN} more)" if len(names) > SHOWN else ""
    )
    return [
        f"the primary {primary} has {len(names)} staged path(s) that differ from its HEAD "
        f"({shown}): after a merge from a worktree this is a stale primary (HEAD moved, the "
        "index and files did not). If nobody is editing there by hand: "
        f"`git -C {primary} restore --source=HEAD --staged --worktree -- . "
        f"'{BOOKKEEPING_EXCLUDE}'`."
    ]


def doctor_notes(repo: Path) -> list[str]:
    """Identity, core.bare and stale-primary notes for the repository at ``repo``."""
    return [*identity_notes(repo), *bare_notes(repo), *stale_primary_notes(repo)]
