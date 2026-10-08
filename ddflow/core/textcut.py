"""One text clipper: ``clip`` cuts text to a budget, in characters or bytes, at a character,
word or line boundary, with one marker policy, and never ends inside a redaction mark.

Nine clippers each did part of this with their own arithmetic (`mcp_bound._clip`,
`markdown._clip`, `bugreport._cut/_tail`, `forge._clip`, `registry.truncate_text` and
`frame.one_line`, `frame.truncate`, `search._snippet`, `store.summarise_row`), and the
variants drifted: one honoured a `[REDACTED:...]` mark and the rest could cut through it.
Each of them is now a call of these three functions with its own options, so the marker,
the budget and the boundary rule are written once.

Pure: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable

#: The start of a redaction mark (`core.redact`): `[REDACTED:<kind>]`. A cut never keeps a
#: half-written mark from here on; a cut inside these ten characters themselves ("[REDA")
#: keeps the fragment, as the clipper this came from always did.
MARK_START = "[REDACTED:"


def _head(text: str, budget: int, unit: str) -> str:
    """The first ``budget`` characters or UTF-8 bytes of ``text`` (a cut character is
    dropped whole, never half-encoded)."""
    if unit == "bytes":
        return text.encode("utf-8")[: max(0, budget)].decode("utf-8", "ignore")
    return text[:budget]


def whole_marks(head: str) -> str:
    """``head`` without a trailing, unfinished ``[REDACTED:...`` mark."""
    start = head.rfind(MARK_START)
    if start != -1 and "]" not in head[start:]:
        return head[:start]
    return head


_MARK = re.compile(r"\[REDACTED:[^\]]*\]")


def _after_mark(text: str, start: int) -> int:
    """``start``, moved to the end of the complete redaction mark it falls inside; ``start``
    itself when it falls inside none."""
    for m in _MARK.finditer(text):
        if m.start() < start < m.end():
            return m.end()
        if m.start() >= start:
            break
    return start


def clip(
    text: str,
    limit: int,
    *,
    keep: int | None = None,
    marker: str = "",
    unit: str = "chars",
    boundary: str = "none",
    side: str = "head",
    rstrip: bool = True,
) -> str:
    """``text`` unchanged when it is at most ``limit`` long (characters, or UTF-8 bytes
    with ``unit="bytes"``); otherwise cut and given ``marker``.

    ``keep`` is how much of the text is kept when it is cut (default ``limit``): a marker
    counted in the budget is ``keep = limit - len(marker)``; one that only pays off when it
    saves something is a ``limit`` of the kept length plus the marker. ``side="head"`` keeps
    the start and puts ``marker`` after it; ``side="tail"`` keeps the end and puts
    ``marker`` before it. ``boundary="word"`` backs the head up to the last space (a
    head with no space is kept whole); ``rstrip`` trims the cut head's trailing whitespace
    before the marker. A head never ends inside a ``[REDACTED:...]`` mark, and a tail never
    starts inside one (it begins after the mark's ``]``).
    """
    size = len(text.encode("utf-8")) if unit == "bytes" else len(text)
    if size <= limit:
        return text
    budget = limit if keep is None else keep
    if side == "tail":
        if unit == "bytes":
            tail = text.encode("utf-8")[-budget:].decode("utf-8", "ignore") if budget > 0 else ""
        else:
            tail = text[-budget:] if budget > 0 else ""
        if tail and unit != "bytes":
            tail = text[_after_mark(text, len(text) - len(tail)) :]
        return marker + tail
    head = whole_marks(_head(text, budget, unit))
    if boundary == "word":
        head = head.rsplit(" ", 1)[0]
    if rstrip:
        head = head.rstrip()
    return head + marker


def window(text: str, start: int, width: int, marker: str = "…") -> str:
    """``width`` characters of ``text`` from ``start``, with ``marker`` on each side that
    has more text beyond it (a snippet around a hit). The window neither starts nor ends
    inside a redaction mark."""
    first = _after_mark(text, start)
    body = whole_marks(text[first : start + width]) if first < start + width else ""
    return (marker if first else "") + body + (marker if start + width < len(text) else "")


def clip_lines(text: str, max_bytes: int, footer: Callable[[int], str]) -> str:
    """``text`` cut to at most ``max_bytes`` UTF-8 bytes on a LINE boundary, ending in
    ``footer(n)``, where n is the count of lines left out.

    The footer counts inside the budget for every prefix (the empty one included), so the
    result never exceeds ``max_bytes`` unless the footer alone does; a shown part never ends
    on blank lines (dropping them grows the footer's count, so the fit is re-checked). When
    not even the first line fits it is cut mid-line on a character boundary and counted as
    not shown; when the footer alone fills the cap it is returned alone. ``max_bytes`` of 0
    or less, or text that already fits, is returned as it is.
    """
    if max_bytes <= 0 or len(text.encode("utf-8")) <= max_bytes:
        return text
    lines = text.splitlines(keepends=True)

    def size(s: str) -> int:
        return len(s.encode("utf-8"))

    # The largest prefix whose size PLUS its own footer fits the cap.
    n, used, best = len(lines), 0, 0
    for k, line in enumerate(lines, 1):
        used += size(line)
        if used + size(footer(n - k)) <= max_bytes:
            best = k
    shown = lines[:best]
    while True:
        while shown and not shown[-1].strip():
            shown.pop()
        if not shown or size("".join(shown)) + size(footer(n - len(shown))) <= max_bytes:
            break
        shown.pop()
    if not shown:
        room = max_bytes - size(footer(n)) - 1
        if room <= 0:
            return footer(n)
        cut = whole_marks(_head(lines[0], room, "bytes"))
        return cut.rstrip("\n") + "\n" + footer(n)
    return "".join(shown) + footer(n - len(shown))
