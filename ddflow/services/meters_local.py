"""Local counts where no vendor reports a percent: transcripts, kilo.db, the ddflow log.

A zero means different things here, and telling them apart is this module's job
(r-no-silent-cost):

* a source was read and nothing happened in the window: a measured zero;
* a turn exists but holds no usable figure: it is `unmetered`, and the total is a lower bound.
  Kilo stores absence as 0 (its columns default to 0, and most of its rows are all zero), so a
  zero row is unmetered, not free;
* only unmetered turns, or a source that could not be read: UNAVAILABLE, never zero.

Kilo's session columns hold a session's whole life, so a session touched once in a window
would count entirely; only its message rows can answer for a window.

`transcript_usage` is the one transcript token reader, with the (message.id, requestId) dedupe:
B-am-ingest's reader replaces its body (B-gap-spend swaps it there), and nothing else here
parses a transcript.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..config_sections.quota import TOKEN_FIELDS
from ..core import clock
from ..infra.store._schema import ReadOnlyError, ReadOnlyTimeout, read_only_rows
from . import meters_base as B

CLAUDE = B.sources()["transcripts"]["harness"]
CLAUDE_SOURCE = "claude-tokens"
KILO = B.sources()["store"]["harness"]
SESSIONS_SOURCE = "ddflow-sessions"


class MeterFailure(Exception):
    """A source that could not be read, with the reason."""


@dataclass(frozen=True)
class TokenRow:
    """One deduped turn. A kind the source does not expose is None, never 0. The token fields
    are named as `TOKEN_FIELDS`, which `[quota].token_fields` draws from."""

    at: float
    model: str
    input: int | None
    output: int | None
    cache_read: int | None = None
    cache_write: int | None = None


@dataclass
class TranscriptUsage:
    rows: list[TokenRow] = field(default_factory=list)
    #: The time of each turn with no usable usage; None when the turn has no time either,
    #: as a torn line has not: it may be a turn, so it counts against every window.
    unmetered: list[float | None] = field(default_factory=list)


def _count(value: object) -> int | None:
    ok = isinstance(value, int) and B.is_number(value) and value >= 0
    return value if ok else None


def _row_of(message: dict[str, Any], at: float | None) -> TokenRow | None:
    usage = message.get("usage")
    if at is None or not isinstance(usage, dict):
        return None
    inp, out = _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
    if inp is None or out is None:
        return None
    return TokenRow(
        at,
        str(message.get("model", "")),
        inp,
        out,
        _count(usage.get("cache_read_input_tokens")),
        _count(usage.get("cache_creation_input_tokens")),
    )


def _merged(old: TokenRow, new: TokenRow) -> TokenRow:
    """One turn seen twice: the largest figure of each kind, as a partial line is the smaller,
    at the latest time, which is when the turn finished. A turn that ended inside a window
    belongs to it whenever it began."""

    def most(a: int | None, b: int | None) -> int | None:
        return b if a is None else a if b is None else max(a, b)

    return TokenRow(
        max(old.at, new.at),
        old.model,
        most(old.input, new.input),
        most(old.output, new.output),
        most(old.cache_read, new.cache_read),
        most(old.cache_write, new.cache_write),
    )


def _candidates(root: Path, since: float, max_bytes: int) -> list[Path]:
    """The transcripts touched since ``since``: an older file cannot hold a newer turn."""
    if not root.is_dir():
        raise MeterFailure(f"no transcript directory at {root}")
    seen = 0
    total = 0
    picked: list[Path] = []
    for path in root.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError as exc:  # gone just now too: a file with no age may hold a recent turn
            raise MeterFailure(f"{path}: {exc}") from exc
        seen += 1
        if st.st_mtime >= since:
            picked.append(path)
            total += st.st_size
    if not seen:
        raise MeterFailure(f"no transcript under {root}")
    if total > max_bytes:
        raise MeterFailure(f"{total} bytes to scan, over [quota].transcript_max_bytes")
    return picked


def _scan(path: Path, since: float, rows: dict, bare: dict) -> None:
    """Every line is parsed: a line that does not parse may be a turn cut anywhere, even before
    it says what it is, so no cheap look at the text can tell it is not one. On a week of
    real transcripts that costs 2.5 s, against 1.7 s for skipping lines by their text."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                bare[(str(path), n)] = None
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            message = message if isinstance(message, dict) else {}
            stamp = entry.get("timestamp")
            at = clock.epoch(stamp, default=-1.0) if isinstance(stamp, str) else -1.0
            at = at if at >= 0 else None
            if at is not None and at < since:
                continue
            mid, rid = message.get("id"), entry.get("requestId")
            key = (mid, rid) if mid and rid else (str(path), n)
            row = _row_of(message, at)
            if row is None:
                held = bare.get(key, at)  # the latest time, as `_merged`; no time beats any
                bare[key] = None if held is None or at is None else max(held, at)
            else:
                rows[key] = _merged(rows[key], row) if key in rows else row


def transcript_usage(root: Path, since: float, max_bytes: int) -> TranscriptUsage:
    """Every turn under ``root`` from ``since`` on, once per (message.id, requestId), subagents
    included. Raises `MeterFailure`: a file it cannot read would make any sum an undercount."""
    usage = TranscriptUsage()
    rows: dict[Any, TokenRow] = {}
    bare: dict[Any, float | None] = {}
    for path in _candidates(root, since, max_bytes):
        try:
            _scan(path, since, rows, bare)
        except OSError as exc:  # gone since `_candidates` too: its turns are not free
            raise MeterFailure(f"{path}: {exc}") from exc
    for key, at in bare.items():
        row = rows.get(key)
        if row is None:
            usage.unmetered.append(at)
        elif at is not None and at > row.at:  # still the same turn, finished later
            rows[key] = replace(row, at=at)
    usage.rows = list(rows.values())
    return usage


def _breakdown(rows: Iterable[TokenRow]) -> dict[str, int]:
    return {kind: sum(getattr(r, kind) or 0 for r in rows) for kind in TOKEN_FIELDS}


def _gaps(ctx: B.MeterContext, rows: Iterable[TokenRow]) -> int:
    """Turns missing a kind the total counts: the total is a lower bound by that many."""
    return sum(1 for r in rows if any(getattr(r, k) is None for k in ctx.token_fields))


def _tokens(
    ctx: B.MeterContext,
    source: str,
    harness: str,
    window: str,
    kinds: dict[str, int],
    unmetered: int,
) -> B.Reading:
    """A token total. Nothing but unmetered turns has no total at all."""
    total = sum(kinds[k] for k in ctx.token_fields)
    if not total and unmetered:
        reason = f"{unmetered} turn(s) in the window hold no token figure"
        return B.unavailable(source, harness, window, "tokens", reason, ctx.now, unmetered)
    return B.Reading(
        source,
        harness,
        window,
        "tokens",
        B.OK,
        float(total),
        B.iso_of(ctx.now),
        False,
        unmetered=unmetered,
        detail=kinds,
    )


def read_claude_tokens(ctx: B.MeterContext) -> list[B.Reading]:
    """Tokens per window from the transcripts, in one pass over them."""
    since = min(ctx.windows.values(), default=ctx.now)
    try:
        usage = transcript_usage(ctx.claude_projects, since, ctx.transcript_max_bytes)
    except MeterFailure as exc:
        return [
            B.unavailable(CLAUDE_SOURCE, CLAUDE, w, "tokens", str(exc), ctx.now)
            for w in ctx.windows
        ]
    out: list[B.Reading] = []
    for window, start in ctx.windows.items():
        rows = [r for r in usage.rows if r.at >= start]
        bare = sum(1 for at in usage.unmetered if at is None or at >= start)
        out.append(
            _tokens(ctx, CLAUDE_SOURCE, CLAUDE, window, _breakdown(rows), bare + _gaps(ctx, rows))
        )
    return out


_KILO_SQL = """
SELECT m.time_created,
       json_extract(m.data, '$.tokens.input'),
       json_extract(m.data, '$.tokens.output'),
       json_extract(m.data, '$.tokens.reasoning'),
       json_extract(m.data, '$.tokens.cache.read'),
       json_extract(m.data, '$.tokens.cache.write'),
       json_extract(m.data, '$.cost')
FROM session s JOIN message m ON m.session_id = s.id AND m.time_created >= ?
WHERE s.time_updated >= ? AND json_extract(m.data, '$.role') = 'assistant'
"""


def _kilo_rows(ctx: B.MeterContext, since: float) -> list[tuple]:
    """The assistant turns from ``since`` on, inside `[quota].kilo_timeout_s`."""
    if not ctx.kilo_db.is_file():
        raise MeterFailure(f"no message store at {ctx.kilo_db}")
    start = int(since * 1000)
    try:
        return read_only_rows(ctx.kilo_db, _KILO_SQL, (start, start), timeout_s=ctx.kilo_timeout_s)
    except ReadOnlyTimeout as exc:
        raise MeterFailure(f"the read ran over its {ctx.kilo_timeout_s}s budget") from exc
    except ReadOnlyError as exc:
        raise MeterFailure(f"{ctx.kilo_db}: {exc}") from exc


def _kilo_token_row(row: tuple) -> TokenRow | None:
    """One turn's figures, or None when they are all zero or missing: Kilo's way of not
    recording them. The same goes for each kind: a zero beside real figures may be a measured
    zero or nothing recorded, and only the second is safe to assume, so it stays None."""
    at, *figures, _cost = row
    inp, out, reasoning, read, write = (_count(v) or None for v in figures)
    if not any((inp, out, reasoning, read, write)):
        return None
    output = None if out is None and reasoning is None else (out or 0) + (reasoning or 0)
    return TokenRow(at / 1000, "", inp, output, read, write)


def _kilo_cost(row: tuple) -> float | None:
    cost = row[-1]
    return float(cost) if B.is_number(cost) and cost > 0 else None


def _usd(ctx: B.MeterContext, window: str, costs: list[float | None]) -> B.Reading:
    """A total only over costs really reported: a cost of 0 is one nobody recorded."""
    known = [c for c in costs if c is not None]
    if not known:
        reason = "the store reported no cost in the window"
        return B.unavailable(KILO, KILO, window, "usd", reason, ctx.now, len(costs))
    return B.Reading(
        KILO,
        KILO,
        window,
        "usd",
        B.OK,
        sum(known),
        B.iso_of(ctx.now),
        False,
        unmetered=len(costs) - len(known),
    )


def read_kilo(ctx: B.MeterContext) -> list[B.Reading]:
    """Tokens and cost per window from Kilo's message rows."""
    since = min(ctx.windows.values(), default=ctx.now)
    try:
        turns = _kilo_rows(ctx, since)
    except MeterFailure as exc:
        return [
            B.unavailable(KILO, KILO, w, unit, str(exc), ctx.now)
            for w in ctx.windows
            for unit in ("tokens", "usd")
        ]
    out: list[B.Reading] = []
    for window, start in ctx.windows.items():
        inside = [t for t in turns if t[0] / 1000 >= start]
        rows = [r for r in map(_kilo_token_row, inside) if r is not None]
        bare = len(inside) - len(rows)
        out.append(_tokens(ctx, KILO, KILO, window, _breakdown(rows), bare + _gaps(ctx, rows)))
        out.append(_usd(ctx, window, [_kilo_cost(t) for t in inside]))
    return out


def read_ddflow_sessions(ctx: B.MeterContext) -> list[B.Reading]:
    """Sessions started per window, from this project's log. One project's count only, so it
    says so: an account-wide session limit is not answered by it alone."""
    if ctx.events is None:
        reason = "no ddflow log was handed to this reader"
        return [
            B.unavailable(SESSIONS_SOURCE, "ddflow", w, "sessions", reason, ctx.now)
            for w in ctx.windows
        ]
    started = [
        (clock.epoch(e.ts, default=-1.0), e) for e in ctx.events if e.kind == "session.started"
    ]
    lost = sum(1 for at, _e in started if at < 0)  # no usable time: it fits no window for sure
    out: list[B.Reading] = []
    for window, start in ctx.windows.items():
        inside = [e for at, e in started if at >= max(start, 0)]
        if lost and not inside:
            reason = f"{lost} session start(s) hold no usable time"
            out.append(
                B.unavailable(SESSIONS_SOURCE, "ddflow", window, "sessions", reason, ctx.now, lost)
            )
            continue
        tools = Counter(str(e.data.get("tool") or "unknown") for e in inside)
        out.append(
            B.Reading(
                SESSIONS_SOURCE,
                "ddflow",
                window,
                "sessions",
                B.OK,
                float(len(inside)),
                B.iso_of(ctx.now),
                False,
                unmetered=lost,
                detail={"scope": "project", "by_tool": dict(tools)},
            )
        )
    return out
