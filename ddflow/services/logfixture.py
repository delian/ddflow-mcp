"""A synthetic event log with the shape of a large real one, for measuring log scale.

The mix follows this project's own log (B-gap-log-scale research): lease renewals are
~43% of events, gate evidence is ~half the bytes, and one item's life is a claim, a
run of heartbeats, a handful of gates (some failing, with reviewer findings) and a
completion. Events are written straight to shards (no fsync, no lock): this makes
fixtures, it is not how the log is written. Deterministic for a seed.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from ..core.events import Event

GATES = ("research", "rules", "implement", "rubber_duck", "critic", "standards", "ci", "unit_tests")
_BASE_AT = 1_790_000_000.0
_WORDS = (
    "lease fold shard event gate render queue claim digest lamport merge review item state "
    "heartbeat evidence snapshot replay hook config schema blob window"
).split()


def _text(rng: random.Random, n_chars: int) -> str:
    out: list[str] = []
    size = 0
    while size < n_chars:
        w = rng.choice(_WORDS)
        out.append(w)
        size += len(w) + 1
    return " ".join(out)


def _evidence(rng: random.Random, failed: bool) -> dict[str, Any]:
    """Gate evidence: mostly small notes, sometimes a long reviewer report."""
    ev: dict[str, Any] = {"note": _text(rng, rng.randint(80, 600)), "tree_sha": "%040x" % rng.getrandbits(160)}
    if failed or rng.random() < 0.15:
        ev["chunk_findings"] = [
            {"chunk": i, "detail": _text(rng, rng.randint(800, 2400))} for i in range(rng.randint(1, 4))
        ]
    return ev


class _Writer:
    def __init__(self, events_dir: Path) -> None:
        self.dir = events_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lamport = 0
        self.count = 0
        self.t = _BASE_AT
        self._open: dict[str, Any] = {}

    def add(self, agent: str, kind: str, subject: str, data: dict[str, Any], dt: float = 1.0) -> None:
        self.lamport += 1
        self.t += dt
        ev = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=self.lamport,
                   ts="2026-10-01T00:00:00.%06dZ" % (self.count % 1_000_000))
        line = ev.to_json() + "\n"
        fh = self._open.get(agent)
        if fh is None:
            fh = self._open[agent] = open(self.dir / f"{agent}.jsonl", "a", encoding="utf-8")
        fh.write(line)
        self.count += 1

    def close(self) -> None:
        for fh in self._open.values():
            fh.close()


def generate(events_dir: Path | str, target_events: int, *, seed: int = 0, agents: int = 40,
             beat_gap_s: float = 120.0, beats: int = 20) -> int:
    """Write about ``target_events`` events under ``events_dir``; return how many.

    ``beat_gap_s`` is the spacing of an agent's heartbeats and ``beats`` how many it
    makes per claim: the knobs of the renewal share (the real log's median gap is ~120 s).
    """
    rng = random.Random(seed)
    w = _Writer(Path(events_dir))
    n = 0
    while w.count < target_events:
        n += 1
        item = f"B-synth-{n}"
        agent = f"synth-{n % agents}"
        w.add(agent, "task.added", item, {
            "title": _text(rng, 60), "body": _text(rng, rng.randint(400, 2400)), "globs": [f"ddflow/synth{n % 97}.py"],
            "needs": [], "parent": "P-synth", "priority": 100, "tags": [],
        })
        at = w.t
        w.add(agent, "lease.acquired", item, {"at": at, "holder": agent, "kind": "task", "ttl_s": 1800,
                                              "globs": [f"ddflow/synth{n % 97}.py"], "note": "", "resources": [],
                                              "worktree": "", "branch": ""})
        w.add(agent, "lease.renewed", item, {"at": at + 0.2, "holder": agent, "branch": f"ddflow/{item}",
                                             "worktree": f"/wt/{item}"})
        w.add(agent, "item.started", item, {})
        gates = list(GATES)
        for i in range(beats):
            at += rng.uniform(0.5, 1.5) * beat_gap_s
            w.t = at
            w.add(agent, "lease.renewed", item, {"at": at, "holder": agent})
            if i % 4 == 3 and gates:
                g = gates.pop(0)
                failed = rng.random() < 0.2
                w.add(agent, "gate.started", item, {"gate": g})
                w.add(agent, "gate.failed" if failed else "gate.passed", item,
                      {"gate": g, "by": agent, "reason": "1 finding(s)" if failed else "",
                       "evidence": _evidence(rng, failed)})
                if failed:
                    for k in range(rng.randint(1, 3)):
                        w.add(agent, "review.triaged", item, {"gate": g, "finding": k + 1, "verdict": "refuted",
                                                              "probe": _text(rng, rng.randint(100, 500))})
        w.add(agent, "lease.released", item, {"holder": agent, "by": agent, "event": "", "note": ""})
        w.add(agent, "item.completed", item, {"forced": False, "kind": "task", "overridden": [], "sha": ""})
    w.close()
    return w.count
