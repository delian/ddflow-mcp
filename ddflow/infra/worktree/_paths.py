"""Worktree paths between the committed log and this machine, plus branch queries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import fsio
from .. import worktree as _W

#: `path` relative to `repo` as a POSIX string, or None outside it: now `fsio.repo_rel`,
#: kept under this name so existing imports keep working.
repo_relative = fsio.repo_rel


def store_path(repo: Path, path: Path | str) -> str:
    """How a worktree path is written INTO the event log: relative to the repo root.

    The log is committed and shared. An absolute path is true only on the machine that
    wrote it, so storing one makes the log say something false on every other checkout:
    a teammate who clones to a different directory, a CI job, and — most sharply — a
    container, where the repo is `/repo` and nothing else on the host is.

    Falls back to an absolute path only when the worktree genuinely lies outside the
    repository tree AND `os.path.relpath` cannot express it portably. That case is
    reported by `ddflow doctor` rather than silently accepted.
    """
    inside = fsio.repo_rel(repo, path)
    if inside is not None:
        return inside
    p = Path(path).resolve()
    try:
        rel = os.path.relpath(p, Path(repo).resolve())
    except ValueError:  # different drive on Windows
        return str(p)
    return Path(rel).as_posix()


def load_path(repo: Path, stored: str) -> Path:
    """Resolve a stored worktree path back to something on THIS machine.

    Absolute stored paths are honoured as-is, because logs written by older versions
    contain them and silently reinterpreting an absolute path as relative would point
    recovery at a directory that does not exist -- which reports "nothing to salvage"
    over real work, the one error this system must never make.
    """
    if not stored:
        return Path()
    p = Path(stored)
    return p if p.is_absolute() else (Path(repo).resolve() / p).resolve()


def branch_exists(repo: Path | str, name: str) -> bool:
    """Is there a local branch ``name``?"""
    return _W.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").ok


def branches(repo: Path, prefix: str = "") -> list[str]:
    """Local branches, optionally filtered to a prefix."""
    r = _W.git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    names = [ln.strip() for ln in r.out.splitlines() if ln.strip()] if r.ok else []
    return [n for n in names if not prefix or n.startswith(prefix)]


def is_merged(repo: Path, branch: str, base: str) -> bool:
    """Is every commit on ``branch`` already reachable from ``base``?

    Asked with `rev-list --count base..branch == 0` rather than `branch --merged`,
    because the latter answers about the CURRENT checkout's HEAD when given no
    argument, and a cleanup pass that silently asked the wrong question would delete
    branches that were not merged at all.
    """
    r = _W.git(repo, "rev-list", "--count", f"{base}..{branch}")
    return r.ok and r.out.strip() == "0"


def absolutise(repo: Path, data: Any) -> Any:
    """Plain-data view with worktree paths resolved to absolute.

    Storage portable, interface usable. The LOG stores worktree paths relative to the
    repo root, which is what makes a committed log true on every checkout; a CALLER needs
    a path it can `cd` to. The conversion happens at the boundary, and it happens HERE
    rather than in `surfaces/context.py` because the api layer hands the same objects to
    the same callers and cannot import a surface to do it.
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in ("worktree", "path"):
        val = out.get(key)
        if isinstance(val, str) and val and not os.path.isabs(val):
            out[key] = str(load_path(repo, val))
    if isinstance(out.get("lease"), dict):
        lv = out["lease"].get("worktree")
        if isinstance(lv, str) and lv and not os.path.isabs(lv):
            out["lease"] = {**out["lease"], "worktree": str(load_path(repo, lv))}
    return out
