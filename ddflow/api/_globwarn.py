"""The warnings about declared files that do not exist, for `claim` and `plan check`
(`[plan].warn_missing_globs`, DX-plan-globs-exist). Never a refusal."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..infra import git as G
from ..services import globcheck as GC


def missing_globs(
    repo: Path, cfg, globs: Iterable[str], tracked: list[str] | None = None
) -> list[str]:
    """One sentence per literal glob in ``globs`` that names nothing in ``repo`` (see
    `services.globcheck`); [] when the knob is off or nothing is wrong. The repository's
    tracked files give the nearest paths; a path on disk counts as existing too."""
    globs = [g for g in globs if GC.literal(g.rstrip("/"))]
    if not globs or not cfg.plan.warn_missing_globs:
        return []
    if tracked is None:
        done = G.run(repo, "ls-files")
        tracked = done.text().splitlines() if done.ok else []
    return [
        m.text() for m in GC.missing(globs, exists=lambda p: (repo / p).exists(), files=tracked)
    ]
