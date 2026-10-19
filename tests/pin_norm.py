"""What a pinned CLI transcript may and may not ignore when it compares text.

A transcript pin is recorded on one Python and replayed on others (the floor, the
interpreter a CI runner ships), and argparse words the SAME message differently from one
release to the next. Two differences are formatting only and are normalised here, on BOTH
sides of a comparison:

* where a ``usage:`` line wraps (3.12 and 3.13 break the line at different places), and
* how ``(choose from a, b)`` quotes the choices (3.12 writes ``'a', 'b'``), and
* how an option with a short AND a long form that takes a value is headed in a help
  listing (3.11/3.12 write ``-m MESSAGE, --message MESSAGE``, 3.13 ``-m, --message MESSAGE``).

Nothing else is touched: the usage words themselves, every other line, the indentation of
help text and the quoting of the offending VALUE (``invalid choice: 'x'``) still have to
match exactly, so a changed message still fails a pin. ``tests/test_pin_norm.py`` holds
the proof of both halves. The golden help snapshots solve the same version problem for
`-h` output with `unwrap_usage` in tests/golden/test_golden_cli_help.py: keep the two in
step if argparse's layout changes again.
"""

from __future__ import annotations

import re
from typing import Any

#: A ``usage:`` block: the line and the indented lines argparse wraps it onto.
_USAGE = re.compile(r"^usage: [^\n]*(?:\n[ \t]+\S[^\n]*)*", re.MULTILINE)
#: ``(choose from 'a', 'b')``: only a list of single-quoted items is argparse's old repr
#: layout; anything else inside the parentheses is left exactly as it is.
#: An item with a comma in it is left alone: stripped, it would read as two choices.
_CHOOSE = re.compile(r"\(choose from ('[^',\n]*'(?:, '[^',\n]*')*)\)")


#: A help-listing option heading in the pre-3.13 spelling: every option string repeats the
#: metavar (``  -m MESSAGE, --message MESSAGE``, ``  -f [FILE ...], --file [FILE ...]``);
#: 3.13 writes it once, after the last string (``-m, --message MESSAGE``). The metavar may
#: hold spaces; the heading ends at the help column (two spaces) or the end of the line.
_OPT_HEADING = re.compile(r"^( +)(-\S+) (\S.*?)((?:, -\S+ \3)+)(?=  |$)", re.MULTILINE)


def _one_metavar(m: re.Match) -> str:
    strings = [m.group(2)] + [part.split(" ", 1)[0] for part in m.group(4).split(", -")[1:]]
    # split(", -") dropped the dash of every later string; put it back
    strings = [strings[0]] + ["-" + s for s in strings[1:]]
    return m.group(1) + ", ".join(strings) + " " + m.group(3)


def option_headings(text: str) -> str:
    """``text`` with every pre-3.13 ``-m MSG, --message MSG`` heading spelt the 3.13 way."""
    return _OPT_HEADING.sub(_one_metavar, text)


def argparse_stable(text: str) -> str:
    """``text`` with argparse's version-dependent formatting made uniform.

    The newer choice layout (``choose from a, b``) cannot tell choices ``['a', 'b']`` from
    ``['a, b']``, so a pin compared through this cannot either; the old one could."""
    text = option_headings(text)
    text = _USAGE.sub(lambda m: " ".join(m.group(0).split()), text)
    return _CHOOSE.sub(lambda m: "(choose from " + m.group(1).replace("'", "") + ")", text)


def stable(value: Any) -> Any:
    """:func:`argparse_stable` over every string inside a transcript row or table."""
    if isinstance(value, str):
        return argparse_stable(value)
    if isinstance(value, (list, tuple)):
        return type(value)(stable(v) for v in value)
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()}
    return value


def rerecord(got: list[list], want: list[list]) -> list[list]:
    """The recorded transcript ``want`` with the rows of ``got`` that differ replaced.

    By command, not by position: a step added to or dropped from a script changes the count,
    and every unchanged step keeps its recorded text byte for byte, so the diff is exactly the
    commands whose output changed. Rows are ``[argv, code, stdout, stderr]``."""
    old: dict[tuple, list[list]] = {}
    for w in want:
        old.setdefault(tuple(w[0]), []).append(w)
    merged = []
    for g in got:
        queue = old.get(tuple(g[0]))
        w = queue.pop(0) if queue else None
        merged.append(w if w is not None and stable(g) == stable(w) else g)
    return merged
