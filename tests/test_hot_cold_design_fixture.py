"""Falsifier plan of D-hot-cold-tiering as executable scenarios (docs/ddflow/decisions/hot-cold.md).

FALSIFIER of the design: two clones and a crash in which any event or evidence blob cannot
be recovered, found by search, or replayed. Each test below is one attempt to build that scenario.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.infra.log import _tier as T

POLICY = T.TierPolicy(enabled=True, summary_every_s=600.0)


def _git_merge(a: Path, b: Path, into: Path) -> None:
    """What git does with two clones: a union of files. A same-named file on both sides with
    different bytes is the conflict the per-agent naming rule exists to make impossible."""
    for src in (a, b):
        for p in Path(src).rglob("*.jsonl"):
            dst = Path(into) / p.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and dst.read_bytes() != p.read_bytes():
                raise FileExistsError(
                    f"merge conflict on {p.relative_to(src)}: two writers, one file"
                )
            dst.write_bytes(p.read_bytes())


def ev(agent: str, n: int, kind: str, subject: str, at: float, **data) -> dict:
    return {
        "agent": agent,
        "id": f"{agent}-{n:05d}",
        "kind": kind,
        "lamport": n,
        "schema": 1,
        "subject": subject,
        "ts": "2026-11-04T00:00:00Z",
        "data": {"at": at, "holder": agent, **data},
    }


def session(
    root: Path, agent: str, base: int, n_renewals: int = 20, step: float = 120.0
) -> list[dict]:
    """A claim, a run of heartbeats, a gate record, a release: what one agent really writes."""
    w = T.TierWriter(root, agent, POLICY)
    out = [ev(agent, base, "lease.acquired", "X-" + agent, 1000.0)]
    out += [
        ev(agent, base + 1 + i, "lease.renewed", "X-" + agent, 1000.0 + step * (i + 1))
        for i in range(n_renewals)
    ]
    out.append(ev(agent, base + 100, "gate.passed", "X-" + agent, 0.0, gate="ci"))
    for e in out:
        w.append(e)
    w.flush("X-" + agent, out[n_renewals])  # release time
    return out


def test_disabled_policy_is_todays_behaviour(tmp_path: Path) -> None:
    w = T.TierWriter(tmp_path, "a-aaaaaa", T.TierPolicy())
    for i in range(5):
        w.append(ev("a-aaaaaa", i, "lease.renewed", "X", 1000.0 + i))
    assert (
        not (tmp_path / "cold" / "a-aaaaaa.jsonl").read_bytes()
        if (tmp_path / "cold" / "a-aaaaaa.jsonl").exists()
        else True
    )
    assert len(T.read_union(tmp_path, tiers=(T.HOT,))) == 5


def test_union_is_the_full_log_and_hot_is_smaller(tmp_path: Path) -> None:
    written = session(tmp_path, "a-aaaaaa", 1)
    union = T.read_union(tmp_path)
    assert {e["id"] for e in written} <= {e["id"] for e in union}  # every event, none lost
    hot = T.read_union(tmp_path, tiers=(T.HOT,))
    assert len(hot) < len(written)
    assert sum(e["kind"] == "lease.renewed" for e in hot) < 20


def test_summary_is_a_lease_renewed_so_an_old_fold_stays_live(tmp_path: Path) -> None:
    written = session(tmp_path, "a-aaaaaa", 1)
    hot = T.read_union(tmp_path, tiers=(T.HOT,))
    assert T.renewed_at(hot) == T.renewed_at(written)  # flush at release: exact
    assert all(e["kind"] in {"lease.acquired", "lease.renewed", "gate.passed"} for e in hot)


def test_liveness_lag_is_bounded_by_the_summary_cadence(tmp_path: Path) -> None:
    w = T.TierWriter(tmp_path, "a-aaaaaa", POLICY)
    evs = [ev("a-aaaaaa", i, "lease.renewed", "X", 1000.0 + 120.0 * i) for i in range(1, 30)]
    for e in evs:
        w.append(e)  # no flush: a holder that is still working
    lag = T.renewed_at(evs)["X"] - T.renewed_at(T.read_union(tmp_path, tiers=(T.HOT,)))["X"]
    assert 0 <= lag < POLICY.summary_every_s
    with pytest.raises(ValueError):
        T.TierPolicy(summary_every_s=1000.0).check(ttl_s=1800.0)  # lag must leave room for the ttl


def test_every_summary_reexpands_with_a_proving_digest(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    refs = [
        T.Ref.of(e["data"]["summary"])
        for e in T.read_union(tmp_path, tiers=(T.HOT,))
        if "summary" in e["data"]
    ]
    assert refs
    assert sum(r.count for r in refs) == 20
    assert sum(len(T.reexpand(tmp_path, r)) for r in refs) == 20


def test_two_clones_merge_without_conflict_and_nothing_is_lost(tmp_path: Path) -> None:
    c1, c2, merged = tmp_path / "c1", tmp_path / "c2", tmp_path / "m"
    a = session(c1, "alice-111111", 1)
    b = session(c2, "bob-222222", 2)
    _git_merge(c1, c2, merged)
    union = T.read_union(merged)
    assert {e["id"] for e in a + b} <= {e["id"] for e in union}
    assert T.renewed_at(T.read_union(merged, tiers=(T.HOT,))) == T.renewed_at(a + b)
    (c2 / "hot").mkdir(exist_ok=True)
    (c2 / "hot" / "alice-111111.jsonl").write_bytes(b"x\n")  # two writers on one file
    with pytest.raises(
        FileExistsError
    ):  # the conflict that per-agent shard naming makes impossible
        _git_merge(c1, c2, tmp_path / "m2")


def test_crash_between_cold_and_summary_loses_nothing(tmp_path: Path) -> None:
    w = T.TierWriter(tmp_path, "a-aaaaaa", POLICY)
    evs = [ev("a-aaaaaa", i, "lease.renewed", "X", 1000.0 + 120.0 * i) for i in range(1, 4)]
    for e in evs:
        w.append(e)  # crash here: cold has them, no summary was written
    del w
    ids = {e["id"] for e in T.read_union(tmp_path)}
    assert {
        e["id"] for e in evs
    } <= ids  # the first renewal was summarised at once; all are in the union
    w2 = T.TierWriter(
        tmp_path, "a-aaaaaa", POLICY
    )  # restart; the next summary covers only what it wrote
    w2.append(ev("a-aaaaaa", 9, "lease.renewed", "X", 5000.0))
    assert {"a-aaaaaa-00009"} <= {e["id"] for e in T.read_union(tmp_path)}


def test_torn_cold_tail_is_skipped_and_counted_not_fatal(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    p = tmp_path / "cold" / "a-aaaaaa.jsonl"
    p.write_bytes(p.read_bytes() + b'{"agent":"a","id":"half')  # power cut mid-write
    events, torn = T._read_lines(p)
    assert torn == 1 and len(events) == 20
    refs = [
        T.Ref.of(e["data"]["summary"])
        for e in T.read_union(tmp_path, tiers=(T.HOT,))
        if "summary" in e["data"]
    ]
    assert sum(len(T.reexpand(tmp_path, r)) for r in refs) == 20  # refs cover whole lines only


def test_altered_cold_range_is_detected_not_trusted(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    p = tmp_path / "cold" / "a-aaaaaa.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"at":1120.0', b'"at":9999.0', 1))
    ref = next(
        T.Ref.of(e["data"]["summary"])
        for e in T.read_union(tmp_path, tiers=(T.HOT,))
        if "summary" in e["data"]
    )
    with pytest.raises(T.DigestMismatch):
        T.reexpand(tmp_path, ref)


def test_clone_that_never_pushed_is_unavailable_never_a_silent_miss(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    (
        tmp_path / "cold" / "a-aaaaaa.jsonl"
    ).unlink()  # the hot summary was pushed, the cold shard was not
    out = T.search(tmp_path, "X-a-aaaaaa")
    assert out["unavailable"], "a missing cold shard must be reported"
    assert any(e["kind"] == "lease.renewed" for e in out["hits"])  # the summary still answers


def test_search_falls_through_to_cold_and_is_bounded(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    full = T.search(tmp_path, "X-a-aaaaaa")
    assert not full["unavailable"] and not full["truncated"]
    assert sum(e["kind"] == "lease.renewed" for e in full["hits"]) >= 20  # cold events are found
    capped = T.search(tmp_path, "X-a-aaaaaa", max_cold_bytes=10)
    assert capped["truncated"] is True  # over budget: said so, not silently dropped


def test_replay_export_of_union_equals_the_untiered_log(tmp_path: Path) -> None:
    """The same events written with tiering off and on: the union is identical, event for event."""
    off, on = tmp_path / "off", tmp_path / "on"
    w_off = T.TierWriter(off, "a-aaaaaa", T.TierPolicy())
    w_on = T.TierWriter(on, "a-aaaaaa", POLICY)
    evs = [
        ev("a-aaaaaa", i, "lease.renewed" if i % 3 else "gate.passed", "X", 1000.0 + 120.0 * i)
        for i in range(1, 40)
    ]
    for e in evs:
        w_off.append(e)
        w_on.append(e)
    only = lambda root: [e for e in T.read_union(root) if "summary" not in e["data"]]  # noqa: E731
    assert json.dumps(only(off), sort_keys=True) == json.dumps(only(on), sort_keys=True)


def test_ddflow_seen_and_unlisted_kinds_stay_hot() -> None:
    assert POLICY.classify("ddflow.seen") == T.HOT  # the skew guard reads the highest stamp
    assert POLICY.classify("gate.passed") == T.HOT
    assert (
        T.TierPolicy(enabled=True, cold_kinds=T.PHASE2_COLD_KINDS).classify("gate.started")
        == T.COLD
    )


def test_shard_owner_without_clone_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        T.TierWriter(tmp_path, "alice", POLICY)


def test_search_honours_the_configured_budget(tmp_path: Path) -> None:
    session(tmp_path, "a-aaaaaa", 1)
    tiny = T.TierPolicy(enabled=True, fall_through_max_bytes=10)
    assert T.search(tmp_path, "X-a-aaaaaa", policy=tiny)["truncated"] is True
