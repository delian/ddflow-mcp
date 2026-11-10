"""Flaky-test rate view and quarantine proposals (PM-flaky).

Reads the machine-local flake log (:mod:`ddflow.services.flakes`) and derives the numbers the
report and the quarantine lane need: flake rate per test and per file over a rolling window,
an estimated retry cost in seconds, and a quarantine proposal (owner + expiry) for any test
that flaked ``quarantine_after`` times within the window.

This module MEASURES and PROPOSES only; the quarantine lane itself is B-sched-flaky's (no
duplicate). There is no second store: the flake log is the one home of flake rows, and every
threshold and interval is a call-site parameter with a default (the store knobs live in
``[perf_metrics]``; this view's knobs are parameters so it stays a non-pin leaf, like
:mod:`ddflow.services.perf.testtimes`). A flake row carries a test id, a timestamp, a gate/item
and a load -- never prompt text, a command argument, a user path or a secret -- and the view
reads it back the same way: ids and counts only.
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ddflow.core import clock
from ddflow.services import flakes


def file_of(test: str) -> str:
    """The file a flake's test id belongs to. A pytest node id (``tests/test_a.py::test_x``)
    is cut at the first ``::``; a pytest FILE id ending in ``.py`` (``tests/test_a.py`` or a
    bare ``test_a.py``) is returned as-is; a unittest id (``pkg.mod.Class.test``) drops the
    trailing CamelCase class and its method and re-joins dots to a path (``pkg/mod.py``) -- so
    flake and duration rows agree on a file id."""
    if "::" in test:
        return test.split("::", 1)[0]
    if test.endswith(".py"):
        return test
    parts = test.split(".")
    # The trailing class (+ any method) is the last segment that names a class: uppercase first.
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] and parts[i][:1].isupper():
            parts = parts[:i]
            break
    return "/".join(parts) + ".py"


def _epoch(row: dict[str, Any]) -> float | None:
    """The row's ``at`` as epoch seconds, or ``None`` when it is absent or not a time. A
    flake with no placeable timestamp is never counted as inside a window."""
    raw = row.get("at")
    if not isinstance(raw, str) or not raw:
        return None
    epoch = clock.epoch(raw, default=float("inf"))
    return None if epoch == float("inf") else epoch


def window_rows(rows: list[dict[str, Any]], now: float, window_s: float) -> list[dict[str, Any]]:
    """The rows whose ``at`` falls in ``[now - window_s, now]``, in their log order. A row
    with a missing or unparseable timestamp cannot be placed and is left out."""
    return [r for r in rows if (e := _epoch(r)) is not None and now - window_s <= e <= now]


def rates(
    rows: list[dict[str, Any]],
    *,
    now: float | None = None,
    window_s: float = 7 * 86400,
    retry_cost_s: float = 60.0,
) -> dict[str, Any]:
    """Per-test and per-file flake rates over the window, plus the estimated retry cost.

    Rate per day = flakes / (window in days), reported BESIDE the raw count so a rate is
    never mistaken for a count. Only ids and counts leave here: no failure text, argument
    or secret. ``now`` defaults to wall time."""
    now = time.time() if now is None else now
    # Clamp to 1s: a non-positive window must not divide by zero.
    window = max(1.0, window_s)
    by_test: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in window_rows(rows, now, window):
        if r.get("test"):
            by_test[str(r["test"])].append(r)
    per_test = [
        {
            "test": test,
            "file": file_of(test),
            "flakes": len(rs),
            "rate_per_day": len(rs) / (window / 86400.0),
            "last_at": max(_epoch(r) for r in rs),
        }
        for test, rs in by_test.items()
    ]
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in per_test:
        by_file[p["file"]].append(p)
    per_file = [
        {
            "file": fl,
            "flakes": sum(p["flakes"] for p in ps),
            "rate_per_day": sum(p["flakes"] for p in ps) / (window / 86400.0),
            "tests": len(ps),
        }
        for fl, ps in sorted(by_file.items(), key=lambda kv: -sum(p["flakes"] for p in kv[1]))
    ]
    in_window = sum(len(rs) for rs in by_test.values())
    return {
        "window_s": window,
        "flakes": in_window,
        "per_test": sorted(per_test, key=lambda p: (-p["flakes"], p["test"])),
        "per_file": per_file,
        "retry_cost_s": in_window * retry_cost_s,
    }


def proposals(
    rows: list[dict[str, Any]],
    *,
    now: float | None = None,
    window_s: float = 7 * 86400,
    quarantine_after: int = 3,
    quarantine_expiry_s: float = 30 * 86400,
) -> list[dict[str, Any]]:
    """One proposal per test that flaked ``quarantine_after`` times within the window.

    Each proposal carries an owner (the item whose gate last flaked this test -- the work that
    was running when it tripped; ``""`` when the log names none -- B-sched-flaky resolves a
    person), an expiry (``now + quarantine_expiry_s``, ISO), and ``proposed`` as a MARKER
    that this is a measure, not an action."""
    now = time.time() if now is None else now
    window = max(1.0, window_s)
    by_test: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in window_rows(rows, now, window):
        if r.get("test"):
            by_test[str(r["test"])].append(r)
    expiry = clock.iso_at(now + quarantine_expiry_s)
    out: list[dict[str, Any]] = []
    for test, rs in sorted(by_test.items()):
        if len(rs) < quarantine_after:
            continue
        newest = max(rs, key=lambda r: _epoch(r) or 0.0)
        out.append(
            {
                "test": test,
                "file": file_of(test),
                "flakes": len(rs),
                "owner": str(newest.get("item") or ""),
                "expires_at": expiry,
                "proposed": "quarantine",
            }
        )
    return out


def report(
    repo: Path,
    *,
    now: float | None = None,
    window_s: float = 7 * 86400,
    quarantine_after: int = 3,
    quarantine_expiry_s: float = 30 * 86400,
    retry_cost_s: float = 60.0,
) -> dict[str, Any]:
    """The rate view read from ``repo``'s flake log: rates + proposals, in one dict.

    A log that cannot be read is ``{"unavailable": True}`` -- never an empty (zero) view."""
    try:
        rows = flakes.read(repo)
    except OSError:
        return {"unavailable": True}
    # One snapshot of ``now`` for BOTH views: rates and proposals must agree on the window
    # edge, so a flake counted by one is never dropped by the other (each resolves ``now``
    # only when it is None -- a collected view resolves it here, once).
    now = time.time() if now is None else now
    view = rates(rows, now=now, window_s=window_s, retry_cost_s=retry_cost_s)
    view["proposals"] = proposals(
        rows,
        now=now,
        window_s=window_s,
        quarantine_after=quarantine_after,
        quarantine_expiry_s=quarantine_expiry_s,
    )
    return view


__all__ = ["file_of", "proposals", "rates", "report", "window_rows"]
