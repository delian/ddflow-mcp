"""Shared bounds for the engines: limits, dotted-path validation, ReDoS-safe regexes.

Python's ``re`` has no timeout, so safety is by construction: a pattern is refused when it
can backtrack exponentially (a repeat of something that itself repeats or branches, or a
back-reference), and the text a pattern sees is length-capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from re import _constants as _c  # type: ignore[attr-defined]
from re import _parser as _p  # type: ignore[attr-defined]


class EngineError(ValueError):
    """A descriptor rule or an input an engine refuses; the message says which and why."""


@dataclass(frozen=True)
class Limits:
    """Every bound an engine applies. Defaults are the shipped values; callers pass their own."""

    max_line_chars: int = 65536  #: a longer stream or transcript line is skipped
    max_value_chars: int = 8192  #: a longer extracted string is cut
    max_pattern_chars: int = 200
    max_match_chars: int = 4096  #: the text a regex sees (the head of the input)
    max_repeat: int = 256  #: the largest explicit {m,n} bound
    max_path_parts: int = 16
    max_rules: int = 64
    max_argv: int = 128
    probe_timeout_s: float = 10.0
    probe_output_bytes: int = 65536


DEFAULT = Limits()
_PATH_PART = re.compile(r"[A-Za-z0-9_-]{1,64}")
_REPEATS = (_c.MAX_REPEAT, _c.MIN_REPEAT, getattr(_c, "POSSESSIVE_REPEAT", _c.MAX_REPEAT))


def check_path(path: object, where: str, limits: Limits = DEFAULT) -> str:
    """A dotted path (``a.b.0``) of plain key names; anything else is refused."""
    if not isinstance(path, str) or not path:
        raise EngineError(f"{where}: a path is a non-empty string")
    parts = path.split(".")
    if len(parts) > limits.max_path_parts or not all(_PATH_PART.fullmatch(p) for p in parts):
        raise EngineError(
            f"{where}: bad path {path[:60]!r} (dotted names of [A-Za-z0-9_-], at most "
            f"{limits.max_path_parts} parts)"
        )
    return path


def _children(op, av):
    if op in _REPEATS:
        return [av[2]]
    if op is _c.SUBPATTERN:
        return [av[3]]
    if op is _c.BRANCH:
        return list(av[1])
    if op in (_c.ASSERT, _c.ASSERT_NOT):
        return [av[1]]
    if op is _c.GROUPREF_EXISTS:
        return [s for s in av[1:] if s]
    if hasattr(_c, "ATOMIC_GROUP") and op is _c.ATOMIC_GROUP:
        return [av]
    return []


def _ambiguous(sub) -> bool:
    """True when ``sub`` holds a branch or a repeat that can run more than once."""
    for op, av in sub:
        if op is _c.BRANCH or (op in _REPEATS and av[1] > 1):
            return True
        if any(_ambiguous(c) for c in _children(op, av)):
            return True
    return False


def _audit(sub, limits: Limits) -> None:
    for op, av in sub:
        if op in (_c.GROUPREF, _c.GROUPREF_EXISTS):
            raise EngineError("back-references are not allowed")
        if op in _REPEATS:
            _lo, hi, body = av
            if hi != _c.MAXREPEAT and hi > limits.max_repeat:
                raise EngineError(f"a repeat bound above {limits.max_repeat}")
            if hi > 1 and _ambiguous(body):
                raise EngineError("a repeat of a repeat or branch can backtrack exponentially")
        for child in _children(op, av):
            _audit(child, limits)


@lru_cache(maxsize=512)
def _compile(pattern: str, max_pattern: int, max_repeat: int) -> re.Pattern[str]:
    lim = Limits(max_pattern_chars=max_pattern, max_repeat=max_repeat)
    try:
        _audit(_p.parse(pattern), lim)
        return re.compile(pattern)
    except EngineError as exc:
        raise EngineError(f"unsafe pattern {pattern[:60]!r}: {exc}") from None
    except (re.error, RecursionError, OverflowError) as exc:
        raise EngineError(f"bad pattern {pattern[:60]!r}: {exc}") from None


def safe_regex(pattern: object, limits: Limits = DEFAULT) -> re.Pattern[str]:
    """Compile ``pattern`` only when it passes the audit; ``EngineError`` otherwise."""
    if not isinstance(pattern, str) or not pattern:
        raise EngineError("a pattern is a non-empty string")
    if len(pattern) > limits.max_pattern_chars:
        raise EngineError(f"pattern longer than {limits.max_pattern_chars} characters")
    return _compile(pattern, limits.max_pattern_chars, limits.max_repeat)


def search(rx: re.Pattern[str], text: str, limits: Limits = DEFAULT) -> re.Match[str] | None:
    """``rx.search`` over the head of ``text`` only (the cap bounds the work)."""
    return rx.search(text[: limits.max_match_chars])


def clip(value: object, limits: Limits = DEFAULT) -> str | int | float | bool | None:
    """A scalar from untrusted JSON, or None; strings are cut to the cap."""
    if isinstance(value, str):
        return value[: limits.max_value_chars]
    if isinstance(value, (bool, int, float)):
        return value
    return None


def expect_keys(table: object, allowed: set[str], where: str) -> dict:
    if not isinstance(table, dict):
        raise EngineError(f"{where}: expected a table")
    extra = set(table) - allowed
    if extra:
        raise EngineError(f"{where}: unknown key(s) {sorted(extra)}; allowed {sorted(allowed)}")
    return table
