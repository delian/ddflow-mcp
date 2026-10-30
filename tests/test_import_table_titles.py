"""A markdown task-table row whose title holds a literal pipe imports whole.

The plan-table convention ``| Id | Title | Needs | Globs |`` writes a title's own ``|``
with a backslash in front of the pipe (``a|b`` -> ``a\\|b``), the same kind of escape
``views/markdown._cell`` writes. A naive ``row.split('|')`` sent the title up to its first
pipe and read the leftover cells as globs: SX-seed imported as ``title='`ddflow seed plan'``
with ``globs=['SX-bundle','KB-layer']`` -- a task id read as a file glob, which the conflict
check then compared against real paths on every unrelated task (B-sx-tasks-pipe-split-titles).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import importer as IM


def _tasks(repo: Path, text: str) -> dict:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "plan.md").write_text(text)
    found, _ = IM.scan_todos(repo, globs=("docs/plan.md",))
    return {f.ident: f for f in found if f.kind == "task"}


PLAN = (
    "## Proposed phase and task list\n\n"
    "| Id | Title | Needs | Globs |\n"
    "|---|---|---|---|\n"
    "| SX-seed | `ddflow seed plan\\|apply`: new repo, init | P.1, P.2 | "
    "ddflow/services/seed.py, ddflow/api/seed.py |\n"
)


def test_a_table_title_with_a_pipe_is_not_cut_at_the_pipe(repo):
    t = _tasks(repo, PLAN)
    assert "SX-seed" in t, "the table row imports as the id of its Id column"
    assert "plan|apply" in t["SX-seed"].title, (
        "the title's backslashed pipe is read back, not read as a column"
    )


def test_a_table_keeps_needs_in_needs_and_globs_in_globs(repo):
    t = _tasks(repo, PLAN)["SX-seed"]
    assert t.needs == ["P.1", "P.2"]
    assert t.globs == ["ddflow/services/seed.py", "ddflow/api/seed.py"]


def test_a_table_row_may_leave_the_globs_cell_off(repo):
    # A GFM row may leave a trailing empty cell off the closing pipe; a globs index past
    # the row's cells is read as 'no globs', never an IndexError.
    short = "## T\n\n| Id | Title | Needs | Globs |\n|---|---|---|---|\n| SX-tiny | t | P.2 |\n"
    t = _tasks(repo, short)["SX-tiny"]
    assert t.title == "t"
    assert t.globs == []
    assert t.needs == ["P.2"]


def test_a_blank_line_ends_a_table_instead_of_reading_as_its_delimiter_row(repo):
    # The delimiter row is only the cells made of '-'/':'. A blank line has none, and it
    # must end the table rather than stand in for that delimiter row (a header followed by
    # a blank line marks no table, and a pipe line after it is prose, not a task).
    no_delim = "## T\n\n| Id | Title |\n\n| X | Task |\n"
    t = _tasks(repo, no_delim)
    assert "X" not in t, "a header with no delimiter row is not a table, so no task imports"
