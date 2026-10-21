"""The pipeline an item runs, decided by its ACTUAL diff, for callers that hold no diff.

`gates.status` and `needs_independent_reviewer` already pick the doc pipeline from the real
changed paths (`docsonly.pipeline_by_diff`); the merge queue, test scope and reports asked
`pipeline_for` and saw the full task pipeline for a docs-only task (B4da2fc502b). This is
the one choke point they share."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.model import Item
from .completion import changed_paths
from .docsonly import pipeline_by_diff
from .gates import kind_pipeline, pipeline_for


def effective_pipeline(repo: Path | None, cfg: Config, item: Item) -> list[str]:
    """`pipeline_for` after the item's actual diff: the doc pipeline for a docs-only change.
    Without a repo (or a readable diff) it is the declared pipeline, never a guess."""
    base = pipeline_for(item, cfg)
    if repo is None:
        return base
    return pipeline_by_diff(
        item, cfg, base, lambda: changed_paths(repo, item), kind_pipeline(cfg, "doc")
    )[0]
