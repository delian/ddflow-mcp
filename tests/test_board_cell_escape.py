"""B2eaa1e5e8e: the board wrote each task's title (and needs, globs, owner) raw into the
QUEUE.md table, so a `|` in a title invented a column and a newline split the row."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli


def _cells(row: str) -> list[str]:
    """A GFM table row's cells, scanned the way cmark-gfm does: a backslash escapes the
    character after it (so `\\\\|` is an escaped backslash and then a delimiter)."""
    return re.findall(r"\|((?:\\.|[^|\\])*)(?=\|)", row.strip())


def test_a_pipe_or_newline_in_a_title_does_not_break_the_board_table(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "phase", "add", "P1", "--title", "Phase one")[0] == 0
    title = "pick a | b\nsecond line, c\\|d\rthird\r\nfourth \\*e\\"
    assert (
        run_cli(
            repo, "task", "add", "T1", "--phase", "P1", "--title", title, "--globs", "src/a|b\\x.py"
        )[0]
        == 0
    )
    board = run_cli(repo, "render", "--show", "board")[1]
    rows = [ln for ln in board.split("\n") if ln.startswith("|")]  # a CR is a break too
    header, _sep, *body = rows
    assert len(body) == 1, board
    assert len(_cells(body[0])) == len(_cells(header)) == 7, body[0]
    assert "\r" not in body[0], repr(body[0])
    # only the pipe and the backslashes before it are escaped: `\\*e\\` is the author's
    assert "pick a \\| b second line, c\\\\\\|d third fourth \\*e\\ |" in body[0], body[0]
    assert "`src/a\\|b\\x.py`" in body[0], body[0]


def test_an_id_with_a_backslash_or_star_keeps_the_bold_span_closed(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "T*1\\", "--title", "t")[0] == 0
    board = run_cli(repo, "render", "--show", "board")[1]
    row = next(ln for ln in board.split("\n") if ln.startswith("| [ ]"))
    assert "**T\\*1\\\\** t |" in row, row
