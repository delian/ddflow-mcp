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

from ..config import LogConfig
from ..core.events import Event
from ..infra.blobstore import externalize

GATES = ("research", "rules", "implement", "ci", "unit_tests", "rubber_duck", "critic", "standards")
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


def _evidence(rng: random.Random, failed: bool, gate: str = "") -> dict[str, Any]:
    """Gate evidence: mostly small notes, sometimes a long reviewer report."""
    ev: dict[str, Any] = {
        "note": _text(rng, rng.randint(80, 600)),
        "tree_sha": "%040x" % rng.getrandbits(160),
    }
    if gate in ("rubber_duck", "critic"):  # a review records how long it took and what it covered
        ev.update(
            elapsed_s=round(rng.uniform(30, 900), 1),
            chunks_total=rng.randint(1, 6),
            reviewed_head="%040x" % rng.getrandbits(160),
            diff_sha="%040x" % rng.getrandbits(160),
        )
    if failed or rng.random() < 0.15:
        ev["chunk_findings"] = [
            {"chunk": i, "detail": _text(rng, rng.randint(800, 2400))}
            for i in range(rng.randint(1, 4))
        ]
    return ev


class _Writer:
    def __init__(self, events_dir: Path, blobs: bool = False, coalesce_s: int = 0) -> None:
        self.dir = events_dir
        self.blobs = blobs
        self.coalesce_s = coalesce_s
        self.last_beat: dict[str, float] = {}
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lamport = 0
        self.count = 0
        self.t = _BASE_AT
        self._open: dict[str, Any] = {}

    def add(
        self, agent: str, kind: str, subject: str, data: dict[str, Any], dt: float = 1.0
    ) -> None:
        if kind == "lease.acquired":
            self.last_beat[subject] = data["at"]
        elif kind == "lease.renewed" and set(data) == {"at", "holder"} and self.coalesce_s:
            if data["at"] - self.last_beat.get(subject, -1e18) < self.coalesce_s:
                return
            self.last_beat[subject] = data["at"]
        if self.blobs and kind.startswith("gate."):
            data = externalize(self.dir.parents[1], LogConfig(evidence_blobs=True), kind, data)
        self.lamport += 1
        self.t += dt
        ev = Event(
            kind=kind,
            subject=subject,
            data=data,
            agent=agent,
            lamport=self.lamport,
            ts="2026-10-01T00:00:00.%06dZ" % (self.count % 1_000_000),
        )
        line = ev.to_json() + "\n"
        fh = self._open.get(agent)
        if fh is None:
            fh = self._open[agent] = open(self.dir / f"{agent}.jsonl", "a", encoding="utf-8")
        fh.write(line)
        self.count += 1

    def close(self) -> None:
        for fh in self._open.values():
            fh.close()


def generate(
    events_dir: Path | str,
    target_events: int,
    *,
    seed: int = 0,
    agents: int = 40,
    beat_gap_s: float = 120.0,
    beats: int = 13,
    blobs: bool = False,
    coalesce_s: int = 0,
) -> int:
    """Write about ``target_events`` events under ``events_dir``; return how many.

    ``blobs`` writes the gate evidence out of line (as a current ddflow does) under the
    project that owns ``events_dir``; ``coalesce_s`` leaves out a heartbeat that close to the
    last one written (as `[lease].renew_log_min_s` does). Both default to the old shape.

    ``beat_gap_s`` is the spacing of an agent's heartbeats and ``beats`` how many it
    makes per claim: the knobs of the renewal share (the real log's median gap is ~120 s).
    """
    rng = random.Random(seed)
    w = _Writer(Path(events_dir), blobs, coalesce_s)
    n = 0
    while w.count < target_events:
        n += 1
        item = f"B-synth-{n}"
        agent = f"synth-{n % agents}"
        w.add(
            agent,
            "task.added",
            item,
            {
                "title": _text(rng, 60),
                "body": _text(rng, rng.randint(400, 2400)),
                "globs": [f"ddflow/synth{n % 97}.py"],
                "needs": [],
                "parent": "P-synth",
                "priority": 100,
                "tags": [],
            },
        )
        at = w.t
        w.add(
            agent,
            "lease.acquired",
            item,
            {
                "at": at,
                "holder": agent,
                "kind": "task",
                "ttl_s": 1800,
                "globs": [f"ddflow/synth{n % 97}.py"],
                "note": "",
                "resources": [],
                "worktree": "",
                "branch": "",
            },
        )
        w.add(
            agent,
            "lease.renewed",
            item,
            {"at": at + 0.2, "holder": agent, "globs": [f"ddflow/synth{n % 97}.py"]},
        )
        w.add(agent, "item.started", item, {})
        for k in range(2):
            w.add(agent, "task.updated", item, {"title": _text(rng, 60), "tags": [f"t{k}"]})
        w.add(agent, "session.prompt", f"S-{n}", {"text": _text(rng, rng.randint(200, 900))})
        gates = list(GATES)
        for i in range(beats):
            at += rng.uniform(0.5, 1.5) * beat_gap_s
            w.t = at
            w.add(agent, "lease.renewed", item, {"at": at, "holder": agent})
            if i % 2 == 1 and gates:
                g = gates.pop(0)
                failed = rng.random() < 0.2
                if g in ("ci", "unit_tests"):  # only command gates announce a start
                    w.add(agent, "gate.started", item, {"gate": g})
                w.add(
                    agent,
                    "gate.failed" if failed else "gate.passed",
                    item,
                    {
                        "gate": g,
                        "by": agent,
                        "reason": "1 finding(s)" if failed else "",
                        "evidence": _evidence(rng, failed, g),
                    },
                )
                if failed:
                    for k in range(rng.randint(1, 3)):
                        w.add(
                            agent,
                            "review.triaged",
                            item,
                            {
                                "gate": g,
                                "finding": k + 1,
                                "verdict": "refuted",
                                "probe": _text(rng, rng.randint(100, 500)),
                            },
                        )
        w.add(
            agent, "lease.released", item, {"holder": agent, "by": agent, "event": "", "note": ""}
        )
        w.add(
            agent,
            "item.completed",
            item,
            {"forced": False, "kind": "task", "overridden": [], "sha": ""},
        )
    w.close()
    return w.count
