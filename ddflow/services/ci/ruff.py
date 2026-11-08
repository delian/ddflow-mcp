"""Ruff as a merge-enforced CI concern that auto-corrects lint/format drift (DX-ci-ruff-gate).

The ci gate runs the project's pre-push stage, whose ruff hooks (`ruff check` and
`ruff format --check` in `.pre-commit-config.yaml`) fail a branch whose changed Python files
carry drift. The gate REPORTED that failure but did not FIX it, so a drifted branch merged and
the next `git push` failed (Bci-ruff-format-check-ci-lint-*). With `[ci].ruff_autofix` on
(default) the item's `ddflow ci run` runs `ruff check --fix` and `ruff format` over the changed
files IN THE ITEM'S OWN WORKTREE and records the corrections, which `api.cirun` notes in the
gate evidence. The gate it recorded stays failed: the agent commits the corrections and
re-runs, so a changed file cannot reach the merge with lint drift.

Ruff is run as `uv run ruff` when the project is a uv project with a lockfile (the pinned
venv the `repo: local` pre-commit hooks use), else the bare `ruff` on PATH -- never a bare
binary when the project pins a version, so the fix formats with the version the check uses.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...infra import git as G
from ...infra import proc as P

#: The file suffixes ruff checks and formats, and so the only ones that can drift.
_PY = (".py", ".pyi")


def ruff_bin(tree: Path) -> list[str]:
    """The argv prefix that runs this repository's ruff, matching the `repo: local` pre-commit
    hooks: `uv run ruff` when the project is a uv project with a lockfile (the pinned venv the
    hook uses), else the bare `ruff` on PATH. [] when there is nothing either way."""
    root = Path(tree)
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run", "ruff"]
    if shutil.which("ruff"):
        return ["ruff"]
    return []


def failing_ruff(checks) -> list[str]:
    """The ids of the failing checks that are the pre-commit ruff hooks; empty when none are.

    The hook names are ``ruff check ...`` and ``ruff format --check ...`` (with a
    ` (CI: Lint)` suffix in this repository's config), so a prefix test holds whatever label a
    project gives to its own hooks."""
    return [c.id for c in checks if not c.ok and _is_ruff(c.id)]


def _is_ruff(check_id: str) -> bool:
    return check_id.startswith("ruff check") or check_id.startswith("ruff format")


def changed_python(repo: Path, sha: str, base: str) -> list[str]:
    """The `.py`/`.pyi` files ``sha`` changed since ``base``: the diff the ruff hooks checked.

    ``base`` is the ref the ci run merged ``sha`` with (``Result.merged_with``); empty means the
    commit itself (`sha^`), so a bare commit is still described."""
    base = base or f"{sha}^"
    names = G.paths(repo, "diff", "--name-only", f"{base}..{sha}") or []
    return [n for n in names if n.endswith(_PY)]


def _snapshot(root: Path, file: str) -> bytes | None:
    try:
        return (Path(root) / file).read_bytes()
    except OSError:
        return None


def _safe_files(root: Path, files: list[str]) -> list[str]:
    """``files`` as the paths that are ``root``'s own Python files, nothing else: an absolute
    path, a ``..`` traversal, or a name that reads as an option is never handed to ruff, which
    would edit a file outside the worktree or parse a name as a flag."""
    root = Path(root).resolve()
    kept: list[str] = []
    for f in files:
        if not f.endswith(_PY) or f.startswith("-") or Path(f).is_absolute():
            continue
        if (root / f).resolve().is_relative_to(root):
            kept.append(f)
    return kept


def auto_fix(worktree: Path, files: list[str], *, timeout_s: int = 120) -> dict:
    """Run `ruff check --fix` then `ruff format` over ``files`` in ``worktree``; the corrections.

    `check --fix` runs FIRST and `format` LAST so a fix that changes layout is left format-clean
    (ruff's own fixes are not formatter-stable). ``reformatted`` and ``fixed`` name the files
    each pass rewrote (a before/after comparison, so a pass that changed nothing is empty);
    ``output`` holds what ruff printed. ``{}`` when there is no Python file to fix;
    ``{"unavailable": ...}`` when there is no ruff to run."""
    argv = ruff_bin(worktree)
    py = _safe_files(worktree, files)
    if not argv:
        return {"unavailable": "no ruff (not a uv project and `ruff` is not on PATH)"}
    if not py:
        return {}
    out: list[str] = []
    reformatted, fixed = [], []
    for label, extra in (("fixed", ("check", "--fix")), ("reformatted", ("format",))):
        before = {f: _snapshot(worktree, f) for f in py}
        p = P.capture([*argv, *extra, "--", *py], timeout=timeout_s, cwd=worktree)
        seen = (p.stdout + p.stderr).strip()
        if seen:
            out.append(seen)
        changed = sorted(
            f for f, data in before.items() if data is not None and _snapshot(worktree, f) != data
        )
        (fixed if label == "fixed" else reformatted).extend(changed)
    return {
        "reformatted": reformatted,
        "fixed": fixed,
        "output": "\n".join(x for x in out if x),
    }
