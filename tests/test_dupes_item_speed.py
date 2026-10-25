"""B098fd705f8: `ddflow dupes <item>` scores only pairs containing the item.

Before the fix the sweep weighed every record against every other and filtered to the
asked record afterwards, so one item on a 3398-record log ran past the 120 s tool
timeout. Scoped to an item, the answer must come from ONE query -- proportional to the
queue size, not its square -- and return in seconds however large the log is.
"""

from __future__ import annotations

import sys
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


def _records(n: int) -> list[dict[str, str]]:
    """``n`` records whose texts share one word pool, so a full sweep scores a dense web
    of pairs -- and the item-scoped sweep must still touch only pairs containing it."""

    out = []
    for i in range(n):
        title = " ".join(_POOL[i % len(_POOL) : i % len(_POOL) + 8]) + f" rec{i}"
        body = " ".join(_POOL[(i * 3) % len(_POOL) : (i * 3) % len(_POOL) + 12])
        out.append({"id": f"R{i}", "kind": "task", "title": title, "body": body})
    return out


def _state(records: list[dict[str, str]]) -> SimpleNamespace:
    """A duck State holding ``records`` as live tasks: what ``pairs._sweep_records`` and
    ``pairs._settled`` read."""

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
    assert all(a == "R149" for a, _ in asked), (
        f"{len(asked)} pair(s) scored, and not all with the asked record first"
    )
    assert all(r["a"] == "R149" or r["b"] == "R149" for r in rows), rows[:3]


def test_dupes_item_on_a_3000_record_queue_scores_only_the_item_side_of_it():
    """On a full-size queue the item side is scored O(queue), never the queue squared:
    the scored-pair count is what is asserted here, not wall-clock (deterministic under
    any load -- `dupes <item>` on a gate text that runs on the full log)."""

    records = _records(3000)
    asked: list[tuple[str, str]] = []
    rows = P.pair_records(
        records,
        floor=0.10,
        item="R1499",
        settled=lambda a, b: asked.append((a, b)) or False,
    )
    assert asked, "the shared vocabulary must make the sweep score pairs"
    assert all(a == "R1499" for a, _ in asked), (
        f"{len(asked)} pair(s) scored, and not all with the asked record first"
    )
    assert len(asked) <= len(records) - 1, (
        f"{len(asked)} pair(s) scored on a {len(records)}-record queue: the item-scoped"
        " sweep must never ask about the pairs between two OTHER records"
    )
    assert rows and all(r["a"] == "R1499" or r["b"] == "R1499" for r in rows)


def test_pairs_from_hands_the_item_down_to_one_query():
    """The `dupes <item>` seam: `pairs_from` must hand the item to `pair_records` so the
    whole answer comes from ONE index query, not a post-sweep filter (roborev: the
    scoping is pinned at the pair_records boundary; this pins the boundary itself)."""

    records = _records(300)
    queries: list[str] = []
    orig = P.sim.Index.query

    def counting_query(self, record, limit=None, kinds=None):
        queries.append(str(record.get("id") or ""))
        return orig(self, record, limit=limit, kinds=kinds)

    P.sim.Index.query = counting_query
    try:
        rows = P.pairs_from(_state(records), Config.load(), kinds=["task"], item="R42")
    finally:
        P.sim.Index.query = orig
    assert queries == ["R42"], f"the sweep queried {queries}, not just the asked record"
    assert rows and all(r["a"] == "R42" or r["b"] == "R42" for r in rows)
