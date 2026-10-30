"""E6 limit_detect: is this run stopped by a usage limit, and when does it reset.

Evidence in a fixed order: an exit code, a canonical stream event, a message regex (bounded
and ReDoS-audited, run over the head of the text). A regex with a ``(?P<reset>...)`` group
supplies the reset text; ``reset_source`` says where else a caller may look.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .safety import DEFAULT, EngineError, Limits, expect_keys, safe_regex, search
from .stream_map import EVENTS

EXIT_MAX = 255
RESET_SOURCES = ("none", "message", "header", "stream")


@dataclass(frozen=True)
class Spec:
    exit_codes: frozenset[int] = frozenset()
    stream_events: frozenset[str] = frozenset()
    messages: tuple = ()  #: compiled safe patterns
    reset_source: str = "none"


@dataclass(frozen=True)
class Hit:
    by: str  #: exit_code | stream_event | message
    reset: str = ""


def parse(table: object, limits: Limits = DEFAULT) -> Spec:
    t = expect_keys(
        table, {"exit_codes", "stream_events", "messages", "reset_source"}, "limit_detect"
    )
    codes = t.get("exit_codes", [])
    if not isinstance(codes, list) or not all(type(c) is int and 0 <= c <= EXIT_MAX for c in codes):
        raise EngineError("limit_detect.exit_codes: a list of integers 0..255")
    evs = t.get("stream_events", [])
    if not isinstance(evs, list) or not all(isinstance(e, str) and e in EVENTS for e in evs):
        raise EngineError(f"limit_detect.stream_events: names from {list(EVENTS)}")
    msgs = t.get("messages", [])
    if not isinstance(msgs, list) or len(msgs) > limits.max_rules:
        raise EngineError("limit_detect.messages: a short list of patterns")
    src = t.get("reset_source", "none")
    if src not in RESET_SOURCES:
        raise EngineError(f"limit_detect.reset_source: one of {list(RESET_SOURCES)}")
    return Spec(frozenset(codes), frozenset(evs), tuple(safe_regex(m, limits) for m in msgs), src)


def detect(
    spec: Spec,
    *,
    exit_code: int | None = None,
    events: Iterable[str] = (),
    text: str = "",
    limits: Limits = DEFAULT,
) -> Hit | None:
    if exit_code is not None and exit_code in spec.exit_codes:
        return Hit("exit_code")
    if spec.stream_events.intersection(events):
        return Hit("stream_event")
    for rx in spec.messages:
        m = search(rx, text, limits)
        if m:
            reset = m.groupdict().get("reset") or ""
            return Hit("message", reset[: limits.max_value_chars])
    return None
