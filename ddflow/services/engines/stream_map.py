"""E5 stream_map: JSON-lines from a running agent to canonical lane events.

Rules are data: ``{when = {path, equals | regex | exists}, emit = event, fields = {name = path}}``.
The first rule that matches a line decides it. Lines that are not objects, are too long or
too deep are counted as skipped, never an error: an agent's stream is untrusted input.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..hookio import _dig
from .safety import (
    DEFAULT,
    EngineError,
    Limits,
    check_path,
    clip,
    expect_keys,
    no_constant,
    safe_regex,
    search,
)

EVENTS = ("started", "tool", "usage", "settled", "limit", "error")


@dataclass(frozen=True)
class Rule:
    path: str
    emit: str
    equals: str | int | float | bool | None = None
    regex: Any = None  #: a compiled safe pattern
    exists: bool = False
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    event: str
    fields: dict[str, Any] = field(default_factory=dict)


_FIELD_NAME = re.compile(r"[a-z_][a-z0-9_]{0,31}")


def _check_when(when: dict, w: str) -> None:
    """Exactly one meaningful test: equals (not null), regex, or exists = true (present and not null)."""
    if "exists" in when and when["exists"] is not True:
        raise EngineError(f"{w}.when.exists: only true is meaningful")
    if "equals" in when and (
        when["equals"] is None or not isinstance(when["equals"], (str, int, float, bool))
    ):
        raise EngineError(f"{w}.when.equals: a string, number or bool (JSON null reads as absent)")
    if sum(k in when for k in ("equals", "regex", "exists")) != 1:
        raise EngineError(f"{w}.when: exactly one of equals, regex, exists")


def _check_fields(fields: object, w: str, limits: Limits) -> dict[str, str]:
    if not isinstance(fields, dict):
        raise EngineError(f"{w}.fields: expected a table of name = path")
    for name, p in fields.items():
        if not _FIELD_NAME.fullmatch(str(name)):
            raise EngineError(f"{w}.fields: bad field name {str(name)[:30]!r}")
        check_path(p, f"{w}.fields.{name}", limits)
    return dict(fields)


def _parse_rule(raw_rule: object, w: str, limits: Limits) -> Rule:
    r = expect_keys(raw_rule, {"when", "emit", "fields"}, w)
    when = expect_keys(r.get("when"), {"path", "equals", "regex", "exists"}, f"{w}.when")
    if not isinstance(r.get("emit"), str) or r["emit"] not in EVENTS:
        raise EngineError(f"{w}: emit must be one of {list(EVENTS)}")
    _check_when(when, w)
    return Rule(
        path=check_path(when.get("path"), f"{w}.when.path", limits),
        emit=r["emit"],
        equals=when.get("equals"),
        regex=safe_regex(when["regex"], limits) if "regex" in when else None,
        exists=bool(when.get("exists")),
        fields=_check_fields(r.get("fields", {}), w, limits),
    )


def parse_rules(rules: object, limits: Limits = DEFAULT) -> tuple[Rule, ...]:
    if not isinstance(rules, list):
        raise EngineError("stream rules: expected a list of tables")
    if len(rules) > limits.max_rules:
        raise EngineError(f"more than {limits.max_rules} stream rules")
    return tuple(_parse_rule(r, f"stream rule {i}", limits) for i, r in enumerate(rules))


def _matches(rule: Rule, raw: dict, limits: Limits) -> bool:
    v = _dig(raw, rule.path)
    if rule.exists:
        return v is not None
    if rule.regex is not None:
        return isinstance(v, str) and search(rule.regex, v, limits) is not None
    return v is not None and type(v) is type(rule.equals) and v == rule.equals


def map_line(line: str, rules: tuple[Rule, ...], limits: Limits = DEFAULT) -> Event | None:
    """The canonical event for one stream line, or None (no rule, or hostile input)."""
    if len(line) > limits.max_line_chars or not line.strip():
        return None
    try:
        raw = json.loads(line, parse_constant=no_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    for rule in rules:
        if _matches(rule, raw, limits):
            vals = {n: clip(_dig(raw, p), limits) for n, p in rule.fields.items()}
            return Event(rule.emit, {n: v for n, v in vals.items() if v is not None})
    return None


class _Stream:
    """An iterator of events that also counts the lines it saw and those that gave none."""

    def __init__(self, lines: Iterable[str], rules: tuple[Rule, ...], limits: Limits):
        self._it, self._rules, self._limits = iter(lines), rules, limits
        self.skipped = 0

    def __iter__(self) -> Iterator[Event]:
        return self

    def __next__(self) -> Event:
        for line in self._it:
            ev = map_line(line, self._rules, self._limits)
            if ev is not None:
                return ev
            self.skipped += 1
        raise StopIteration


def map_stream(lines: Iterable[str], rules: tuple[Rule, ...], limits: Limits = DEFAULT) -> _Stream:
    return _Stream(lines, rules, limits)
