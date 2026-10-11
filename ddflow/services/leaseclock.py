"""Whose clock judges a lease (B5a6651cd8e).

A lease's `at` is the claimant's `time.time()`. For a lease of THIS clone that is our own
clock and means what it says. For a lease of ANOTHER clone it is a foreign clock: compared
with ours, a claimant 2 h behind made a live claim look expired ("take it with --force") and
one 2 h ahead made a dead claim last 2 h more. Lamport order is unaffected; only liveness
read wall time.

So for another clone's lease the renewal is timed from when THIS clone first saw it. The
first sight of each renewal is kept in `.ddflow/local/lease-seen` (a LocalStore document:
machine-local, never committed). A dead lease still lapses -- no new renewal is seen for
TTL + grace of our own clock -- and losing the file fails SAFE: the TTL restarts from the next
read, a live lease never looks expired. No daemon, no server.

`localize` rewrites the folded lease's `renewed_at` and nothing else; the fold itself stays pure
(contests compare the claims' own `at`), and a lease without a recorded clone, or of this
clone, keeps today's rule untouched and writes no entry.
"""

from __future__ import annotations

import contextlib
from typing import Any

from ..core.records import State
from ..infra.localstore import LocalStore

#: The document under `.ddflow/local`; its `data` maps a lease's granting event id to
#: `{"renewed_at": <as recorded>, "seen": <our clock when first seen>}`.
LEDGER = "lease-seen"
LEDGER_SCHEMA = 1


def foreign(lease: Any, mine: str) -> bool:
    """True when ``lease`` was claimed from another clone than ``mine`` (one that says which)."""
    clone = getattr(lease, "clone", "")
    return bool(clone) and clone != mine


def localize(st: State, log: Any, now: float) -> State:
    """Time every lease of another clone from when this clone first saw it renewed.

    Mutates and returns ``st``. Never raises: a ledger that cannot be read or written leaves
    the lease timed from ``now`` (live for one more TTL), the safe direction.
    """
    mine = log.clone_id(create=False)
    others = [
        it.lease
        for it in st.items.values()
        if it.lease is not None
        and it.lease.event
        and foreign(it.lease, mine)
        and not getattr(it.lease, "_localized", False)
    ]
    if not others:
        return st
    store = LocalStore(log.root / ".ddflow" / "local", clock=lambda: now)
    ledger = _read(store)
    changed = False
    for lz in others:
        row, fresh = _row(ledger.get(lz.event), lz.renewed_at, now)
        ledger[lz.event] = row
        changed = changed or fresh
        lz.renewed_at = min(float(row["seen"]), now)
        lz._localized = True  # type: ignore[attr-defined]  # a second call must not re-time it
    live_ids = {lz.event for lz in others}
    kept = {k: v for k, v in ledger.items() if k in live_ids}
    if changed or len(kept) != len(ledger):
        with contextlib.suppress(Exception):
            store.update(
                LEDGER,
                lambda cur: _merge(cur, kept),
                schema=LEDGER_SCHEMA,
                default={},
                derived=True,
            )
    return st


def _read(store: LocalStore) -> dict[str, Any]:
    try:
        ledger = store.read(LEDGER, schema=LEDGER_SCHEMA, default={}, derived=True)
    except Exception:
        return {}
    return dict(ledger) if isinstance(ledger, dict) else {}


def _row(row: Any, recorded: float, now: float) -> tuple[dict[str, Any], bool]:
    """The ledger row for a lease renewed (as recorded) at ``recorded``, and whether it is new:
    the first sight, or a later renewal than the one the row remembers."""
    known = (
        isinstance(row, dict)
        and isinstance(row.get("seen"), (int, float))
        and isinstance(row.get("renewed_at"), (int, float))
        and recorded <= float(row["renewed_at"])
    )
    if known:
        return row, False
    return {"renewed_at": recorded, "seen": now}, True


def _merge(current: Any, ours: dict[str, Any]) -> dict[str, Any]:
    """Under the lock: ours, but an earlier first sight another process just wrote wins."""
    cur = current if isinstance(current, dict) else {}
    out = dict(ours)
    for k, v in cur.items():
        if (
            k in out
            and isinstance(v, dict)
            and v.get("renewed_at") == out[k].get("renewed_at")
            and isinstance(v.get("seen"), (int, float))
            and v["seen"] < out[k]["seen"]
        ):
            out[k] = v
    return out
