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
    post-tool hook). ``source`` names the harness/feed; ``ok`` is encoded into the digest and
    a missing duration is the -1 sentinel, never a fabricated 0."""
    import time

    from ddflow.services.perf import store

    store.append(
        repo,
        "agent_calls",
        store.Sample(
            time.time(),
            f"{tc.agent}:{tc.lane}:{tc.item}",
            tc.duration_s if tc.duration_s is not None else -1.0,
            tc.tool_class,
            f"{source}:{1 if tc.ok else 0}",
        ),
    )


def rollup(repo) -> list[dict]:
    """per agent/lane/item/tool-class: {count, ok, failed, total_s} from the local ring."""
    from ddflow.services.perf import store

    by: dict[tuple[str, str, str, str], dict] = defaultdict(
        lambda: {"count": 0, "ok": 0, "total_s": 0.0}
    )
    for s in store._read_ring(repo, "agent_calls"):
        agent, lane, item = ([*s.env.split(":"), "", "", ""])[:3]
        b = by[(agent, lane, item, s.gate)]
        b["count"] += 1
        b["ok"] += int(s.digest.endswith(":1") if s.digest else True)
        if s.value >= 0:  # a recorded duration (the -1 sentinel is a count-only call)
            b["total_s"] += s.value
    return [
        {
            "agent": a,
            "lane": ln,
            "item": it,
            "tool_class": tc,
            "count": b["count"],
            "ok": b["ok"],
            "failed": b["count"] - b["ok"],
            "total_s": b["total_s"],
        }
        for (a, ln, it, tc), b in sorted(by.items())
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
