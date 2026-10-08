"""The regex safety check the pattern modes use: `search --regex` today, `rule search --regex`
once B-uni-search-core.3-callers moves it here.

`regex` runs Python's backtracking engine, which cannot be interrupted, so a pattern is
checked before it runs (`check_regex`): too long, a back-reference, a variable-length repeat
inside a repeat (unless together they run at most `MAX_BRANCH_REPS` times), an alternation
inside a repeat that can run more than `MAX_BRANCH_REPS` times, or more than
`MAX_OPEN_REPEATS` unbounded repeats are refused with the reason.
"""

from __future__ import annotations

import re
from re import _parser as sre_parse  # type: ignore[attr-defined]

MAX_PATTERN = 200
MAX_OPEN_REPEATS = 2
MAX_BRANCH_REPS = 8  # an alternation may sit inside a repeat that runs at most this often


class SearchError(ValueError):
    """The request cannot be answered as asked; the message says what to change."""


def _walk(sre_parse, nodes, reps: int, stats: dict[str, int]) -> None:
    """Refuse what makes the backtracking engine exponential.

    `reps` is how often the enclosing repeats can run the node: 1 outside any repeat, the
    product of their upper bounds, and `MAXREPEAT` once one is unbounded."""
    c = sre_parse
    for op, av in nodes:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            lo, hi, sub = av
            unbounded = hi >= c.MAXREPEAT
            if reps > 1 and lo != hi and (unbounded or reps * hi > MAX_BRANCH_REPS):
                raise SearchError(
                    "regex refused: a repeat of variable length inside another repeat "
                    "can take exponential time"
                )
            if unbounded:
                stats["open"] += 1
            _walk(c, sub, c.MAXREPEAT if unbounded else min(reps * hi, c.MAXREPEAT), stats)
        elif name == "BRANCH":
            if reps > MAX_BRANCH_REPS:
                raise SearchError(
                    "regex refused: an alternation inside a repeat can take exponential "
                    "time (use a character class, or repeat at most "
                    f"{MAX_BRANCH_REPS} times)"
                )
            for alt in av[1]:
                _walk(c, alt, reps, stats)
        elif name in ("GROUPREF", "GROUPREF_EXISTS"):
            raise SearchError("regex refused: back-references can take exponential time")
        elif name == "SUBPATTERN":
            _walk(c, av[3], reps, stats)
        elif name in ("ASSERT", "ASSERT_NOT"):
            _walk(c, av[1], reps, stats)
        elif name == "ATOMIC_GROUP":
            _walk(c, av, reps, stats)


def check_regex(pattern: str, *, ignore_case: bool = True) -> re.Pattern[str]:
    """Compile `pattern` if it is safe to run; otherwise raise SearchError saying why."""
    if len(pattern) > MAX_PATTERN:
        raise SearchError(f"regex refused: longer than {MAX_PATTERN} characters")
    try:
        tree = sre_parse.parse(pattern)
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except (re.error, RecursionError, OverflowError) as exc:
        raise SearchError(f"regex does not compile: {exc}") from None
    stats = {"open": 0}
    _walk(sre_parse, tree, 1, stats)
    if stats["open"] > MAX_OPEN_REPEATS:
        raise SearchError(
            f"regex refused: {stats['open']} unbounded repeats (at most {MAX_OPEN_REPEATS})"
        )
    return rx
