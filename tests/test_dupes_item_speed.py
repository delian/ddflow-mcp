"""B098fd705f8: `ddflow dupes <item>` scores only pairs containing the item.

Before the fix the sweep weighed every record against every other and filtered to the
asked record afterwards, so one item on a 3398-record log ran past the 120 s tool
timeout. Scoped to an item, the answer must come from ONE query — proportional to the
queue size, not its square — and return in seconds however large the log is.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api.knowledge import pairs as P
from ddflow.config import Config
from ddflow.core.records import Item

_POOL = (
    "parser token empty line drops trailing exporter writes timezone report header "
    "generated wrong worktree adopt existing refresh stale merge branch cli flag "
    "option queue sweep pair score duplicate log index store build query record "
    "lesson memory decision research bug task phase claim gate merge complete "
).split()

#: Seconds the item-scoped sweep may take on a 3000-record queue in a test run. The
#: unfixed code weighed 3000 records against each other and took ~28 s on this machine;
#: the fixed code answers from one query and takes well under a second. The bound sits
#: an order of magnitude above the one and below the other.
_BOUND_S = 10.0


def _records(n: int) -> list[dict[str, str]]:
    """``n`` records whose texts share one word pool, so a full sweep scores a dense web
    of pairs — and the item-scoped sweep must still touch only pairs containing it."""

    out = []
    for i in range(n):
        title = " ".join(_POOL[i % len(_POOL) : i % len(_POOL) + 8]) + f" rec{i}"
        body = " ".join(_POOL[(i * 3) % len(_POOL) : (i * 3) % len(_POOL) + 12])
        out.append({"id": f"R{i}", "kind": "task", "title": title, "body": body})
    return out


def _state(records: list[dict[str, str]]) -> SimpleNamespace:
    """A duck State holding ``records`` as live tasks: what ``pairs._sweep_records`` and
    ``pairs._settled`` read (items, memories, links, lessons)."""

    return SimpleNamespace(
        items={r["id"]: Item(id=r["id"], kind="task", title=r["title"], body=r["body"]) for r in records},
        bugs={},
        lessons={},
        decisions={},
        research={},
        memories={},
        links={},
    )


def test_dupes_item_scores_only_pairs_that_contain_it():
    """The settled hook sees every pair a sweep scores: with an item named, no pair
    between two OTHER records may ever be weighed."""

    records = _records(300)
    asked: list[tuple[str, str]] = []
    rows = P.pair_records(
        records,
        floor=0.10,
        item="R149",
        settled=lambda a, b: asked.append((a, b)) or False,
    )
    assert asked, "the shared vocabulary must make the sweep score pairs"
    assert all("R149" in pair for pair in asked), (
        f"{len(asked)} pair(s) scored, and not all contain the asked record"
    )
    assert all("R149" in (r["a"], r["b"]) for r in rows), rows[:3]


def test_dupes_item_on_a_3000_record_queue_stays_bounded():
    """`dupes <item>` on a 3000-record queue must finish within a bounded time and show
    only the asked record's pairs: the gate text runs this on the full log."""

    records = _records(3000)
    st = _state(records)
    t0 = time.perf_counter()
    rows = P.pairs_from(st, Config.load(), kinds=["task"], item="R1499")
    elapsed = time.perf_counter() - t0
    assert elapsed < _BOUND_S, f"item-scoped sweep of {len(records)} records took {elapsed:.1f}s"
    assert rows and all("R1499" in (r["a"], r["b"]) for r in rows)
