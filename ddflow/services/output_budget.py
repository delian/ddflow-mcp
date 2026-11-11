"""One shared output budget for the read commands an agent drives (TE-bounded-outputs).

`show`, `list`, `status`, `gate status`, `review` and `recall` print the whole answer to a
person at their terminal; an agent's tool result is counted, not read. One module serves
both: when `DDFLOW_AGENT` is set, or `--brief` is given, the answer is held to a line and
character budget and ends in a footer naming the exact FULL call and the count cut. A
`--json` body keeps its schema and is only cut when the caller asks for the bounded form.

This is the `[output]` capping story (`views/output_caps.py`, which bounds `board`,
`doctor`, `next` and `status`'s ROW lists) generalised for the prose answers: the two units
are LINES and CHARACTERS, characters standing for tokens at `ddflow.core.budget`'s rate.
The character budget is `[output].max_chars` (the one `ddflow config output.max_chars N`
already sets); the line budget is the `MAX_LINES` default below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..core.budget import CHARS_PER_TOKEN as _CHARS_PER_TOKEN

#: The agent/`--brief` form holds a prose answer to this many lines before the footer.
MAX_LINES = 60

#: The character budget when the caller passes no config (the shipped `[output].max_chars`).
DEFAULT_CHARS = 30000


def is_agent() -> bool:
    """An agent context: the harness marker `DDFLOW_AGENT` is set. (`--agent NAME` tags a
    log shard and is deliberately NOT `is_agent()` -- a person can pass it too.)"""
    return bool(os.environ.get("DDFLOW_AGENT"))


def wants_bounded(a: Any) -> bool:
    """Bounded when the caller passed `--brief` (its steam of names) or is an agent context."""
    get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
    return bool(get("brief", False)) or is_agent()


def budget_chars(cfg: Any) -> int:
    """The character budget in force: `[output].max_chars` when a config is given and it is
    set (0 lifts the cap), else the shipped `DEFAULT_CHARS`."""
    try:
        val = getattr(getattr(cfg, "output", None), "max_chars", None)
    except AttributeError:
        val = None
    if val is None:
        return DEFAULT_CHARS
    try:
        return int(val)
    except (TypeError, ValueError):
        return DEFAULT_CHARS


@dataclass(frozen=True)
class Cap:
    """The bounded form: the text (whole when not cut) and its one-line footer."""

    text: Any
    footer: str
    cut_chars: int
    cut_lines: int


def cut_text(text: Any, *, chars: int = 0, lines: int = MAX_LINES) -> tuple[Any, int, int]:
    """``text`` cut to ``lines`` lines, then ``chars`` characters (on a line boundary);
    returns (kept, cut_chars, cut_lines). A body under both caps is returned whole."""
    if not isinstance(text, str):
        return text, 0, 0
    kept = text
    cut_lines = 0
    cut = kept.split("\n")
    if lines and len(cut) > lines:
        cut_lines = len(cut) - lines
        kept = "\n".join(cut[:lines])
    cut_chars = 0
    if chars and len(kept) > chars:
        at = kept.rfind("\n", 0, chars)
        if at >= 0:  # the kept newline stays: every line shown is whole
            cut_chars = len(kept) - at - 1
            kept = kept[: at + 1]
        else:  # one line longer than `chars`: cut mid-line (there is no boundary to stop at)
            cut_chars = len(kept) - chars
            kept = kept[:chars]
    return kept, cut_chars, cut_lines


def footer(cut_chars: int, cut_lines: int, *, cli: str, mcp: str, chars: int, lines: int) -> str:
    """The one-line footer naming the count cut and the exact FULL call.

    ``cli`` and ``mcp`` are the un-bounded call each surface would run. The footer is
    empty when nothing was cut -- a read under the budget names nothing.
    """
    cut: list[str] = []
    if cut_lines:
        cut.append(f"{cut_lines} more line(s) not shown")
    if cut_chars:
        cut.append(f"{cut_chars} character(s) cut (~{cut_chars // _CHARS_PER_TOKEN} tokens)")
    if not cut:
        return ""
    how = ", ".join(cut)
    first = f"[{how} (budget {lines} line(s)/{chars} character(s))."
    if cli and mcp:
        first += f" everything: `{cli}` (MCP: `{mcp}`)"
    elif mcp:
        first += f" everything: `{mcp}`"
    elif cli:
        first += f" everything: `{cli}`"
    return first + "]"


def apply(
    text: Any,
    *,
    a: Any = None,
    cfg: Any = None,
    cli: str = "",
    mcp: str = "",
    lines: int = MAX_LINES,
    bounded: bool | None = None,
) -> Cap:
    """The budgeted form of ``text`` for an agent.

    ``text`` is returned whole when it is a non-string body (`--json`: the wire schema is
    unchanged unless the caller asked for the bounded form), or when ``bounded`` is False
    (a person, no `--brief`, no `DDFLOW_AGENT`). Otherwise it is cut and a footer is set.

    ``bounded`` overrides the caller/`DDFLOW_AGENT` probe when given (``a`` still probes).
    """
    if bounded is None:
        bounded = wants_bounded(a) if a is not None else is_agent()
    if not bounded or not isinstance(text, str):
        return Cap(text, "", 0, 0)
    chars = budget_chars(cfg)
    kept, cut_chars, cut_lines = cut_text(text, chars=chars, lines=lines)
    return Cap(
        kept,
        footer(cut_chars, cut_lines, cli=cli, mcp=mcp, chars=chars, lines=lines),
        cut_chars,
        cut_lines,
    )
