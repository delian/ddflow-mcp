"""PROTOTYPE of the hot/cold event tiering of D-hot-cold-tiering. Not wired into anything.

Stdlib and `core.digest` only: it exists so the design's claims are executable
(tests/test_hot_cold_design_fixture.py) before any production change relies on them.
The design is docs/ddflow/decisions/hot-cold.md; read it first.

Model: a COLD event is an ordinary event line, written to a per-agent cold shard
(`cold/<agent>.jsonl`, one writer per file, like the hot shards) BEFORE the hot shard
gets a SUMMARY: an ordinary `lease.renewed` (so an older ddflow folds it unchanged)
carrying a reference (shard, start, end, sha256, count) to the cold byte range it
stands for. Nothing is deleted or rewritten; the union of hot and cold shards, ordered
by (lamport, id), is the full log that replay, export and verify read.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.digest import hasher

HOT = "hot"
COLD = "cold"

#: The kinds whose fold effect a summary reproduces exactly. `ddflow.seen` is NOT here:
#: the skew guard reads the highest stamp, and 824 events are not worth a risk.
DEFAULT_COLD_KINDS = frozenset({"lease.renewed"})
#: Phase 2, only after the phase-1 measurement: counted by the summary's `count` field.
PHASE2_COLD_KINDS = DEFAULT_COLD_KINDS | {"gate.started", "gate.out_of_order"}


@dataclass(frozen=True)
class TierPolicy:
    """Declared as data: `[tier]` in config. `enabled=False` is today's behaviour."""

    enabled: bool = False
    cold_kinds: frozenset[str] = DEFAULT_COLD_KINDS
    summary_every_s: float = 600.0  # must be <= ttl_s / 2 (validated by the config check)
    fall_through_max_bytes: int = 1_048_576  # a search reads at most this much cold per query

    def classify(self, kind: str) -> str:
        return COLD if self.enabled and kind in self.cold_kinds else HOT

    def check(self, ttl_s: float) -> None:
        if self.summary_every_s * 2 > ttl_s:
            raise ValueError("tier.summary_every_s must be at most half of lease.ttl_s")


@dataclass(frozen=True)
class Ref:
    """Where cold events live: enough to re-expand a summary and to prove it."""

    shard: str
    start: int
    end: int
    sha256: str
    count: int

    def to_data(self) -> dict[str, Any]:
        return {
            "shard": self.shard,
            "start": self.start,
            "end": self.end,
            "sha256": self.sha256,
            "count": self.count,
        }

    @staticmethod
    def of(d: dict[str, Any]) -> Ref:
        return Ref(d["shard"], int(d["start"]), int(d["end"]), d["sha256"], int(d["count"]))


#: A shard name ends in the writing clone's id (as `_agent.py` makes agent ids): one writer per file.
_CLONE_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


class Unavailable(Exception):
    """The cold range is not in this clone (not pulled yet). Reported, never a silent miss."""


class DigestMismatch(Exception):
    """The cold range differs from what the summary recorded."""


def _line(ev: dict[str, Any]) -> bytes:
    return json.dumps(ev, sort_keys=True, separators=(",", ":")).encode() + b"\n"


@dataclass
class _Pending:
    start: int
    end: int
    count: int
    last_at: float
    holder: str
    h: Any = field(default_factory=hasher)


class TierWriter:
    """One agent's writer. Cold line first (fsync), summary second: a ref never dangles."""

    def __init__(self, root: Path, agent: str, policy: TierPolicy) -> None:
        if not _CLONE_SUFFIX.search(agent):
            raise ValueError(
                f"shard owner {agent!r} must end in -<6 hex clone id> (one writer per shard)"
            )
        self.root, self.agent, self.policy = Path(root), agent, policy
        (self.root / "hot").mkdir(parents=True, exist_ok=True)
        (self.root / "cold").mkdir(parents=True, exist_ok=True)
        self.hot_path = self.root / "hot" / f"{agent}.jsonl"
        self.cold_path = self.root / "cold" / f"{agent}.jsonl"
        self._pending: dict[str, _Pending] = {}  # subject -> run of cold lines not yet summarised
        self._last_summary: dict[str, float] = {}

    def _append(self, path: Path, data: bytes) -> tuple[int, int]:
        with open(path, "ab") as fh:
            start = fh.tell()
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return start, start + len(data)

    def append(self, ev: dict[str, Any]) -> None:
        if self.policy.classify(ev["kind"]) == HOT:
            self._append(self.hot_path, _line(ev))
            return
        raw = _line(ev)
        start, end = self._append(self.cold_path, raw)  # 1. cold, durable
        at = float(ev.get("data", {}).get("at", 0.0))
        p = self._pending.get(ev["subject"])
        if p is None:
            p = self._pending[ev["subject"]] = _Pending(
                start, end, 0, at, ev.get("data", {}).get("holder", self.agent)
            )
        p.end, p.count, p.last_at = end, p.count + 1, max(p.last_at, at)
        p.h.update(raw)
        if at - self._last_summary.get(ev["subject"], -1e18) >= self.policy.summary_every_s:
            self._summarise(ev["subject"], ev)

    def flush(self, subject: str, template: dict[str, Any]) -> None:
        """Summarise now: at release, completion, before a long wait, on shutdown."""
        if subject in self._pending:
            self._summarise(subject, template)

    def _summarise(self, subject: str, template: dict[str, Any]) -> None:
        p = self._pending.pop(subject)
        ref = Ref(f"cold/{self.agent}.jsonl", p.start, p.end, p.h.hexdigest(), p.count)
        summary = dict(template)
        summary["id"] = template["id"] + "-s"
        summary["lamport"] = template["lamport"]
        summary["data"] = {**template.get("data", {}), "at": p.last_at, "summary": ref.to_data()}
        self._append(self.hot_path, _line(summary))  # 2. the summary, after the cold bytes
        self._last_summary[subject] = p.last_at


def _read_lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Whole lines only: a torn tail (crash mid-write) is skipped and counted, as the hot reader does."""
    events: list[dict[str, Any]] = []
    torn = 0
    data = path.read_bytes() if path.exists() else b""
    for raw in data.split(b"\n"):
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except ValueError:
            torn += 1
    return events, torn


def read_union(root: Path, *, tiers: tuple[str, ...] = (HOT, COLD)) -> list[dict[str, Any]]:
    """The full log: every hot and cold shard, ordered (lamport, id), duplicates by id dropped."""
    seen: dict[str, dict[str, Any]] = {}
    for tier in tiers:
        for p in sorted((Path(root) / tier).glob("*.jsonl")):
            for ev in _read_lines(p)[0]:
                seen.setdefault(ev["id"], ev)
    return sorted(seen.values(), key=lambda e: (e["lamport"], e["id"]))


def reexpand(root: Path, ref: Ref) -> list[dict[str, Any]]:
    """The events a summary stands for, proven by digest."""
    path = Path(root) / ref.shard
    if not path.exists():
        raise Unavailable(f"{ref.shard} is not in this clone; pull the clone that wrote it")
    blob = path.read_bytes()[ref.start : ref.end]
    if hasher(blob).hexdigest() != ref.sha256:
        raise DigestMismatch(ref.shard)
    return [json.loads(x) for x in blob.split(b"\n") if x]


def renewed_at(events: list[dict[str, Any]]) -> dict[str, float]:
    """The fold's view of liveness: the latest renewal per subject. Summaries are renewals."""
    out: dict[str, float] = {}
    for ev in events:
        if ev["kind"] == "lease.renewed":
            out[ev["subject"]] = max(out.get(ev["subject"], 0.0), float(ev["data"]["at"]))
    return out


def search(
    root: Path, needle: str, *, max_cold_bytes: int | None = None, policy: TierPolicy | None = None
) -> dict[str, Any]:
    """Hot first; a summary whose text matches falls through to its cold range, bounded.

    Returns hits plus `unavailable` (refs not in this clone) and `truncated` (budget hit):
    a miss is never reported as "no results".
    """
    budget = (
        (policy or TierPolicy()).fall_through_max_bytes
        if max_cold_bytes is None
        else max_cold_bytes
    )
    hits: list[dict[str, Any]] = []
    unavailable: list[str] = []
    truncated = False
    for ev in read_union(root, tiers=(HOT,)):
        ref = ev.get("data", {}).get("summary")
        if needle in json.dumps(ev):
            hits.append(ev)
        if ref and needle in json.dumps(
            {"subject": ev["subject"], "agent": ev["agent"], "kind": ev["kind"]}
        ):
            r = Ref.of(ref)
            if r.end - r.start > budget:
                truncated = True
                continue
            budget -= r.end - r.start
            try:
                hits.extend(reexpand(root, r))
            except Unavailable as e:
                unavailable.append(str(e))
    return {"hits": hits, "unavailable": unavailable, "truncated": truncated}
