"""A markdown task-table row whose title holds a literal pipe imports whole.

The plan-table convention ``| Id | Title | Needs | Globs |`` writes a title's own ``|``
escaped (``a|b`` -> ``a\\|b``), the same escaping ``views/markdown._cell`` writes. A naive
``row.split('|')`` sent the title up to its first pipe and read the leftover cells as
globs: SX-seed imported as ``title='`ddflow seed plan'`` with ``globs=['SX-bundle','KB-layer']``
-- a task id read as a file glob, which the conflict check then compared against real
paths on every unrelated task (B-sx-tasks-pipe-split-titles).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import importer as IM

PLAN = (
    "## Proposed phase and task list\n\n"
    "| Id | Title | Needs | Globs |\n"
    "|---|---|---|---|\n"
    "| SX-seed | `ddflow seed plan\\|apply`: new repo, init | P.1, P.2 | "
    "ddflow/services/seed.py, ddflow/api/seed.py |\n"
)


def _tasks(repo: Path) -> dict:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "plan.md").write_text(PLAN)
    found, _ = IM.scan_todos(repo, globs=("docs/plan.md",))
    return {f.ident: f for f in found if f.kind == "task"}


def test_a_table_title_with_a_pipe_is_not_cut_at_the_pipe(repo):
    t = _tasks(repo)
    assert "SX-seed" in t, "the table row imports as the id of its Id column"
    assert "plan|apply" in t["SX-seed"].title, (
        "the title's escaped pipe is read back, not read as a column"
    )


def test_a_table_keeps_needs_in_needs_and_globs_in_globs(repo):
    t = _tasks(repo)["SX-seed"]
    assert t.needs == ["P.1", "P.2"]
    assert t.globs == ["ddflow/services/seed.py", "ddflow/api/seed.py"]
