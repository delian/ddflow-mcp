"""B2eaa1e5e8e: the board wrote each task's title (and needs, globs, owner) raw into the
QUEUE.md table, so a `|` in a title invented a column and a newline split the row."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli


def _cells(row: str) -> list[str]:
    """A GFM table row split on its UNESCAPED pipes, outer ones dropped."""
    return re.split(r"(?<!\\)\|", row.strip())[1:-1]


def test_a_pipe_or_newline_in_a_title_does_not_break_the_board_table(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "phase", "add", "P1", "--title", "Phase one")[0] == 0
    title = "pick a | b\nsecond line"
    assert (
        run_cli(
            repo, "task", "add", "T1", "--phase", "P1", "--title", title, "--globs", "src/a|b.py"
        )[0]
        == 0
    )
    board = run_cli(repo, "render", "--show", "board")[1]
    rows = [ln for ln in board.splitlines() if ln.startswith("|")]
    header, _sep, *body = rows
    assert len(body) == 1, board
    assert len(_cells(body[0])) == len(_cells(header)) == 7, body[0]
    assert "pick a \\| b second line" in body[0], body[0]
    assert "src/a\\|b.py" in body[0], body[0]
