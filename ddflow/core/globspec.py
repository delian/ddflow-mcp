"""Reading a list of path globs the way an agent writes one.

The plain comma-separated string (`config.csv_list`) is the main notation; this adds the
two other shapes agents actually send, and refuses what can only be a mangled one:

- several values (a repeated flag, a list over MCP) are ALL kept, each read as a comma
  list. A plain store kept the last one, and an agent that declared ten paths held one
  (Bdc85898c40);
- a JSON array of strings, passed as ONE value, is read as that list. Split on its
  commas, it became `'["a.py"'` and `'"b.py"]'` -- globs that match nothing, so nothing
  was protected. A comma inside a glob is only expressible this way;
- a glob still carrying a JSON/shell quote, or an unbalanced bracket, is refused: a
  real path has neither, and accepting it is a claim that silently covers nothing.

`add`, `update` and `split` read their globs with `parse`; `claim` (through
`leases.acquire`) refuses what `problem` names. A surface that splits a value on commas
BEFORE it gets here has already lost a JSON array: it arrives mangled, and is refused
rather than recorded.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..config import csv_list


def _one(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            got = json.loads(text)
        except ValueError:
            got = None
        if isinstance(got, list) and all(isinstance(g, str) for g in got):
            return [g.strip() for g in got if g.strip()]
    return csv_list(text)


def parse(raw: str | Iterable[str] | None) -> list[str]:
    """Every glob in ``raw`` -- a string, or several -- in order, without repeats."""
    if raw is None:
        return []
    parts = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for part in parts:
        for g in _one(str(part)):
            if g not in out:
                out.append(g)
    return out


def problem(globs: Iterable[str]) -> str:
    """Why ``globs`` cannot be what the caller meant, or "".

    Only shapes a real path will not have: a double quote, or a single quote at either
    end (a JSON or shell fragment left in), or a `[` without its `]` (a character class
    cut in half by a comma split).
    """
    bad: list[str] = []
    for g in globs:
        open_ = False  # inside a character class, a second '[' is literal
        for ch in g:
            if ch == "[" and not open_:
                open_ = True
            elif ch == "]" and open_:
                open_ = False
        if '"' in g or g.startswith("'") or g.endswith("'") or open_:
            bad.append(g)
    if not bad:
        return ""
    return (
        f"these globs cannot be paths: {', '.join(repr(b) for b in bad)} -- a quote or an "
        f"unbalanced bracket is left over from a JSON array or shell quoting that was "
        f"split on its commas. Pass the paths comma-separated, without quotes or "
        f"brackets: a.py,b/**. Nothing was recorded."
    )
