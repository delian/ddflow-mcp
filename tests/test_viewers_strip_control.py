"""The text renderer strips terminal control sequences from a raw record title
(B-B743bfde582): an operator stores a title with an ANSI SGR (`\x1b[31m`) or an OSC
(`\x1b]0;...BEL`); `task list` replays it stripped, so it never re-renders on the caller's
terminal."""
from __future__ import annotations

from ddflow.surfaces.commands.viewers_lists import _CONTROL, _line


def test_the_title_render_strips_terminal_control_sequences():
    row = {"id": "T1", "state": "open", "title": "red \x1b[31mtitle\x1b[0m", "owner": "", "updated": "2026"}
    line = _line("task", row)
    assert "\x1b" not in line
    assert "\x1b[31mtitle" not in line
    assert "red title" in line


def test_an_osc_sequence_and_bare_controls_are_stripped_too():
    row = {"id": "T2", "state": "open", "title": "a\x1b]0;pwned\x07b\x07c", "owner": "", "updated": "2026"}
    assert "\x1b" not in _line("task", row)
    assert "\x07" not in _line("task", row)


def test_the_control_strip_is_not_vacuous():
    assert "\x1b" not in _CONTROL.sub("", "plain title \x1b[31mx")
    assert "\x1b" in "plain title \x1b[31mx"
