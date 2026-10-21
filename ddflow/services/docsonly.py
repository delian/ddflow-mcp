"""Which tasks changed only documents (bug B-research-task-cannot-complete-docs-only).

Kept beside the gates, not in them: `gates.status` and `completion` both ask it, and the gates
package re-exports every public definition it makes."""

from __future__ import annotations

from collections.abc import Callable

from ..config import Config
from ..core.globs import inside
from ..core.model import Item


def diff_is_docs_only(item: Item, cfg: Config, paths: list[str] | None) -> bool:
    """Did this TASK change only documents (every ACTUAL changed path inside
    `[gates].doc_globs`)? Then it runs the doc pipeline: a research or docs task made through
    the MCP, which has no `--kind`, has no code for `unit_tests` or an independent reviewer to
    judge (B-research-task-cannot-complete-docs-only). The ACTUAL diff decides, never the
    declared globs (B-na-required-gate): no readable diff, an empty one, a promotion and a
    phase are never docs-only."""
    if item.kind != "task" or item.promote_to or not paths or not cfg.gates.doc_globs:
        return False
    return all(any(inside(p, d) for d in cfg.gates.doc_globs) for p in paths)


def pipeline_by_diff(
    item: Item,
    cfg: Config,
    gates: list[str],
    diff: Callable[[], list[str] | None] | None,
    doc_pipeline: list[str],
) -> tuple[list[str], Callable[[], list[str] | None] | None]:
    """The pipeline an item runs given its ACTUAL diff: ``doc_pipeline`` when it changed only
    documents, else ``gates``. Also returns ``diff`` read once, so the caller's later
    `applies_when` checks do not run git again."""
    if diff is None:
        return gates, None
    paths = diff()
    return (doc_pipeline if diff_is_docs_only(item, cfg, paths) else gates), lambda: paths
