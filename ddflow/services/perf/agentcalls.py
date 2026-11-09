"""Agent activity: tool-call counts and time per agent, lane and item (PM-agent-calls).

The counter only: a tool call is recorded into the perf store's local ring keyed by
``agent:lane:item`` -- its tool-name CLASS (a small enum, never the arguments), whether it
succeeded, and its duration when the harness gives one. No prompt text, command arguments,
output, user paths or secrets is ever a sample value. Optional harness-telemetry importers
(Claude Code OpenTelemetry, the session JSONL CCusage reads) go behind an ``Importer`` port
with a fake; a real importer that is not wired is ``unavailable``, never zero. Counts,
durations and cost only, marked approximate and versioned (those formats change).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

#: The tool-name CLASS recorded (a classification, never the call's arguments or output).
TOOL_CLASSES = ("shell", "edit", "read", "write", "search", "gate", "mcp", "other")


@dataclass(frozen=True)
class ToolCall:
    agent: str
    lane: str
    item: str
    tool_class: str  # one of TOOL_CLASSES
    ok: bool
    duration_s: float | None = None  # None: the harness gave no duration (never 0)


class Importer:
    """A harness-telemetry importer's port: one ``series -> ToolCall`` fold. A REAL importer
    (Claude Code OTel, session JSONL) implements this; the local ring is the other source."""

    def call_records(self, source: str) -> list[ToolCall]:
        raise NotImplementedError


class FakeImporter(Importer):
    """The op-in fake a test runs: returns canned records for one source file. Never wired."""

    def __init__(self, records: list[ToolCall] | None = None):
        self._records = records or []

    def call_records(self, source: str) -> list[ToolCall]:
        return list(self._records)


def record(repo, source: str, tc: ToolCall) -> None:
    """Append one tool call to the local ring under ``agent_calls`` (the caller is the
    post-tool hook). ``source`` names which harness/feed saw it (for the doctor listing)."""
    import time

    from ddflow.services.perf import store

    store.append(
        repo,
        "agent_calls",
        store.Sample(
            time.time(),
            f"{tc.agent}:{tc.lane}:{tc.item}",
            tc.duration_s
            if tc.duration_s is not None
            else 1.0,  # a count is 1, a duration its seconds
            tc.tool_class,
            source,
        ),
    )


def rollup(repo) -> list[dict]:
    """tool-class -> {count, ok, failed, total_s} over the local ring, per agent/lane/item."""
    from ddflow.services.perf import store

    rows = store._read_ring(repo, "agent_calls")
    by: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"count": 0, "ok": 0, "total_s": 0.0}
    )
    for s in rows:
        agent, lane, _item = ([*s.env.split(":"), "", "", ""])[:3]
        b = by[(agent, lane, s.gate)]
        b["count"] += 1
        b["ok"] += int(s.digest and not getattr(s, "digest", "") == "err")
        if s.value != 1.0:
            b["total_s"] += s.value
    return [
        {
            "agent": a,
            "lane": ln,
            "tool_class": tc,
            "count": b["count"],
            "ok": b["ok"],
            "total_s": b["total_s"],
        }
        for (a, ln, tc), b in sorted(by.items())
    ]


NOT_COLLECTED = {
    "claude_code_otel": "an importer behind Importer (Claude Code OpenTelemetry JSONL); not wired yet",
    "session_jsonl": "the Codex/Claude session JSONL (ccusage) importer; not wired yet",
}


__all__ = [
    "NOT_COLLECTED",
    "TOOL_CLASSES",
    "FakeImporter",
    "Importer",
    "ToolCall",
    "record",
    "rollup",
]
