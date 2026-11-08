"""Ruff as a merge-enforced CI concern that auto-corrects lint/format drift (DX-ci-ruff-gate).

The ci gate runs the project's pre-push stage, whose ruff hooks (`ruff check` and
`ruff format --check` in `.pre-commit-config.yaml`) fail a branch whose changed Python files
carry drift. The gate REPORTED that failure but did not FIX it, so a drifted branch merged and
the next `git push` failed (Bci-ruff-format-check-ci-lint-*). With `[ci].ruff_autofix` on
(default) the item's `ddflow ci run` runs `ruff format` and `ruff check --fix` over the changed
files IN THE ITEM'S OWN WORKTREE and returns the corrections, which `api.cirun` records in the
gate evidence. The gate it recorded stays failed: the agent commits the corrections and re-runs,
so a changed file cannot reach the merge with lint drift.

The fix commands are DATA about the checkout, not about this repository: ruff is run as the
bare `ruff` on PATH where a tool is installed, else as `uv run ruff` (the venv the project's
`uv.lock` pins, which is what the `repo: local` pre-commit hooks use).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...infra import git as G
from ...infra import proc as P

#: The file suffixes ruff checks and formats, and so the only ones that can drift.
_PY = (".py", ".pyi")


def ruff_bin() -> list[str]:
    """The argv prefix that runs this repository's ruff: the bare binary when it is on PATH,
    `uv run ruff` otherwise (the pinned venv the `repo: local` pre-commit hooks use), [] when
    there is none either way."""
    if shutil.which("ruff"):
        return ["ruff"]
    if shutil.which("uv"):
        return ["uv", "run", "ruff"]
    return []


def failing_ruff(checks) -> list[str]:
    """The ids of the failing checks that are the pre-commit ruff hooks; empty when none are.

    The pre-commit hook names are ``ruff check ...`` and ``ruff format --check ...`` (with a
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


def auto_fix(worktree: Path, files: list[str], *, timeout_s: int = 120) -> dict:
    """Run `ruff format` and `ruff check --fix` over ``files`` in ``worktree``; the corrections.

    ``reformatted`` and ``fixed`` name the files each pass rewrote (a before/after comparison,
    so a pass that changed nothing is empty); ``output`` holds what ruff printed. ``{}`` when
    there is no Python file to fix; ``{"unavailable": ...}`` when there is no ruff to run."""
    argv = ruff_bin()
    py = [f for f in files if f.endswith(_PY)]
    if not argv:
        return {"unavailable": "no ruff (`ruff` not on PATH and no `uv` to `uv run ruff`)"}
    if not py:
        return {}
    out: list[str] = []
    reformatted, fixed = [], []
    for label, extra in (("reformatted", ("format",)), ("fixed", ("check", "--fix"))):
        before = {f: _snapshot(worktree, f) for f in py}
        p = P.capture([*argv, *extra, *py], timeout=timeout_s, cwd=worktree)
        seen = (p.stdout + p.stderr).strip()
        if seen:
            out.append(seen)
        changed = sorted(
            f for f, data in before.items() if data is not None and _snapshot(worktree, f) != data
        )
        (reformatted if label == "reformatted" else fixed).extend(changed)
    return {
        "reformatted": reformatted,
        "fixed": fixed,
        "output": "\n".join(x for x in out if x),
    }
