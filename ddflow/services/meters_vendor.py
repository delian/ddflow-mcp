"""The vendor's own percent and reset for Claude Code (D-quotas-governor (3)).

Three sources, three shapes, one result:

* `~/.claude.json` `cachedUsageUtilization`: percent 0-100, ISO reset, `fetchedAtMs`. Often
  days old, which is why a reading says when it was fetched and whether it is stale.
* the statusline's stdin `rate_limits`: percent 0-100, reset in epoch seconds.
* a stream-json `rate_limit_event`: utilization 0 to 1, reset in epoch seconds.

The last two arrive as events, so they are kept in one snapshot file that a reader can open
later. A source with no figure for a window writes nothing, and the last real figure ages
out through `stale`: no write here ever puts a zero in place of a missing number. The
snapshot has no account in it, because neither event names one.

ddflow never calls the vendor's usage endpoint: that would spend the user's credentials
unasked (D-quotas-governor (7)).
"""

from __future__ import annotations

import json
from typing import Any

from ..core import clock
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import locked
from . import meters_base as B
from .quota import account_tag

HARNESS = "claude-code"
#: The vendor's window names -> ours. Per-model and overage windows are not quota windows here.
WINDOW_OF = {"five_hour": "5h", "seven_day": "week"}
CACHE_SOURCE = "claude-cache"
SNAPSHOT_SOURCE = "claude-snapshot"
SNAPSHOT_VERSION = 1
#: Percent per unit of each source's own figure.
STATUSLINE_SCALE = 1.0
STREAM_SCALE = 100.0
CACHE_SCALE = 1.0


def _none(ctx: B.MeterContext, source: str, reason: str) -> list[B.Reading]:
    return [
        B.unavailable(source, HARNESS, window, "percent", reason, ctx.now)
        for window in WINDOW_OF.values()
    ]


def _reset_of(raw: object) -> float | None:
    """A reset as epoch seconds: an ISO string or a number. None when it is neither."""
    if isinstance(raw, str):
        at = clock.epoch(raw, default=-1.0)
        return at if at >= 0 else None
    if B.is_number(raw) and raw > 0:
        return float(raw)
    return None


def _figure(
    ctx: B.MeterContext,
    source: str,
    window: str,
    value: float,
    observed: float,
    resets: float | None,
    account: str = "",
    detail: dict[str, Any] | None = None,
) -> B.Reading:
    return B.Reading(
        source,
        HARNESS,
        window,
        "percent",
        B.OK,
        value,
        B.iso_of(observed),
        B.is_stale(ctx, observed, resets),
        resets_at="" if resets is None else B.iso_of(resets),
        account=account,
        detail=detail or {},
    )


def _load_cache(ctx: B.MeterContext) -> dict[str, Any]:
    """The `cachedUsageUtilization` table. Raises `ValueError`/`OSError` with the reason."""
    data = json.loads(ctx.claude_state.read_text(encoding="utf-8"))
    cache = data.get("cachedUsageUtilization") if isinstance(data, dict) else None
    if not isinstance(cache, dict):
        raise ValueError("it holds no cachedUsageUtilization")
    return cache


def read_claude_cache(ctx: B.MeterContext) -> list[B.Reading]:
    """The percent Claude Code last cached, one reading per window."""
    try:
        cache = _load_cache(ctx)
    except (OSError, ValueError) as exc:
        return _none(ctx, CACHE_SOURCE, f"{ctx.claude_state}: {exc}")
    fetched = cache.get("fetchedAtMs")
    if not B.is_number(fetched):
        return _none(ctx, CACHE_SOURCE, "the cache has no fetchedAtMs, so its age is unknown")
    uuid = cache.get("accountUuid")
    account = account_tag(uuid) if isinstance(uuid, str) and uuid else ""
    table = cache.get("utilization")
    table = table if isinstance(table, dict) else {}
    out: list[B.Reading] = []
    for vendor, window in WINDOW_OF.items():
        row = table.get(vendor)
        row = row if isinstance(row, dict) else {}
        value = B.percent(row.get("utilization"), CACHE_SCALE)
        if value is None:
            reason = f"the cache has no figure for {vendor}"
            out.append(B.unavailable(CACHE_SOURCE, HARNESS, window, "percent", reason, ctx.now))
            continue
        resets = _reset_of(row.get("resets_at"))
        out.append(_figure(ctx, CACHE_SOURCE, window, value, fetched / 1000, resets, account))
    return out


def _read_snapshot(ctx: B.MeterContext) -> dict[str, Any]:
    """The snapshot's windows. Raises `ValueError`/`OSError` with the reason."""
    data = json.loads(ctx.snapshot.read_text(encoding="utf-8"))
    windows = data.get("windows") if isinstance(data, dict) else None
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(windows, dict) or version is True or version != SNAPSHOT_VERSION:
        raise ValueError("not a snapshot of this version")
    return windows


def read_claude_snapshot(ctx: B.MeterContext) -> list[B.Reading]:
    """The percent from the last statusline or stream-json event, one reading per window."""
    try:
        windows = _read_snapshot(ctx)
    except (OSError, ValueError) as exc:
        return _none(ctx, SNAPSHOT_SOURCE, f"{ctx.snapshot}: {exc}")
    out: list[B.Reading] = []
    for window in WINDOW_OF.values():
        row = windows.get(window)
        row = row if isinstance(row, dict) else {}
        value = B.percent(row.get("percent"), 1.0)
        observed = _reset_of(row.get("observed_at"))
        if value is None or observed is None:
            reason = f"the snapshot has no figure for {window}"
            out.append(B.unavailable(SNAPSHOT_SOURCE, HARNESS, window, "percent", reason, ctx.now))
            continue
        detail = {"origin": str(row.get("origin", ""))}
        resets = _reset_of(row.get("resets_at"))
        out.append(_figure(ctx, SNAPSHOT_SOURCE, window, value, observed, resets, detail=detail))
    return out


def _is_newer(row: object, now: float) -> bool:
    """Was ``row`` observed after ``now``? A writer delayed on its way to the lock must not put
    an older figure over a newer one."""
    observed = _reset_of(_table(row).get("observed_at"))
    return observed is not None and observed > now


def _write(ctx: B.MeterContext, figures: dict[str, dict[str, Any]], origin: str) -> int:
    """Merge ``figures`` over the snapshot, window by window. A corrupt snapshot is derived
    data, so a fresh event replaces it. A newer figure stays. How many windows were written."""
    if not figures:
        return 0
    ctx.snapshot.parent.mkdir(parents=True, exist_ok=True)
    stamped = {
        window: {**row, "observed_at": B.iso_of(ctx.now), "origin": origin}
        for window, row in figures.items()
    }
    with locked(ctx.snapshot):
        try:
            windows = _read_snapshot(ctx)
        except (OSError, ValueError):
            windows = {}
        fresh = {w: row for w, row in stamped.items() if not _is_newer(windows.get(w), ctx.now)}
        if fresh:
            body = {"version": SNAPSHOT_VERSION, "windows": {**windows, **fresh}}
            atomic_write(ctx.snapshot, json.dumps(body, sort_keys=True), mode=0o600)
    return len(fresh)


def _row(value: float | None, resets: object) -> dict[str, Any] | None:
    if value is None:
        return None
    at = _reset_of(resets)
    return {"percent": value, "resets_at": "" if at is None else B.iso_of(at)}


def _parse(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _table(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def snapshot_statusline(ctx: B.MeterContext, text: str) -> int:
    """Take the percents out of a statusline's stdin. How many windows were written; 0 for a
    session with no `rate_limits`, whose figures are simply not known."""
    limits = _table(_parse(text).get("rate_limits"))
    figures: dict[str, dict[str, Any]] = {}
    for vendor, window in WINDOW_OF.items():
        row = _table(limits.get(vendor))
        got = _row(B.percent(row.get("used_percentage"), STATUSLINE_SCALE), row.get("resets_at"))
        if got:
            figures[window] = got
    return _write(ctx, figures, "statusline")


def snapshot_stream_line(ctx: B.MeterContext, line: str) -> int:
    """Take the percents out of one stream-json line, if it is a `rate_limit_event` carrying
    them. Either `unifiedWindows`, or one `rateLimitType` with its `utilization`. A refusal
    with no utilization says the limit was hit but gives no figure, so it writes nothing."""
    data = _parse(line)
    if data.get("type") != "rate_limit_event":
        return 0
    info = _table(data.get("rate_limit_info"))
    unified = _table(info.get("unifiedWindows"))
    single = info.get("rateLimitType")
    figures: dict[str, dict[str, Any]] = {}
    for vendor, window in WINDOW_OF.items():
        rows = [_table(unified.get(vendor))] + ([info] if single == vendor else [])
        for row in rows:
            got = _row(B.percent(row.get("utilization"), STREAM_SCALE), row.get("resetsAt"))
            if got:
                figures[window] = got
                break
    return _write(ctx, figures, "stream-json")
