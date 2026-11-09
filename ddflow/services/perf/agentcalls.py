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

import json
from collections import defaultdict
from dataclasses import dataclass


def _js(parts: list[str]) -> str:
    return json.dumps(parts, separators=(",", ":"))


def _unjs(s: str) -> list[str]:
    try:
        out = json.loads(s)
        return ([*out, "", "", ""])[:3] if isinstance(out, list) else [s, "", ""]
    except ValueError:
        return [s, "", ""]


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
            _js([tc.agent, tc.lane, tc.item]),
            tc.duration_s if tc.duration_s is not None else -1.0,
            tc.tool_class,
            f"{source}:{1 if tc.ok else 0}",
        ),
    )


def rollup(repo) -> list[dict]:
    """per agent/lane/item/tool-class: {count, ok, failed, total_s} from the local ring."""
    from ddflow.services.perf import store

    by: dict[tuple[str, str, str, str], dict] = defaultdict(
        lambda: {"count": 0, "ok": 0, "timed": 0, "total_s": 0.0}
    )
    for s in store._read_ring(repo, "agent_calls"):
        agent, lane, item = _unjs(s.env)
        b = by[(agent, lane, item, s.gate)]
        b["count"] += 1
        b["ok"] += int(s.digest.endswith(":1"))  # a missing digest is not a success
        if s.value >= 0:  # a recorded duration (the -1 sentinel is a count-only call)
            b["total_s"] += s.value
            b["timed"] += 1
    return [
        {
            "agent": a,
            "lane": ln,
            "item": it,
            "tool_class": tc,
            "count": b["count"],
            "ok": b["ok"],
            "failed": b["count"] - b["ok"],
            "timed": b["timed"],
            "total_s": b["total_s"],
        }
        for (a, ln, it, tc), b in sorted(by.items())
    ]


def doctor(repo) -> list[dict]:
    """Which agent-call sources are in use and which are available, so the operator can turn
    one off: the hook and any wired importer each count their own ring rows (read off the
    digest's source prefix); a not-wired importer is unavailable, never a zero count."""
    from ddflow.services.perf import store

    counts: dict[str, int] = defaultdict(int)
    for s in store._read_ring(repo, "agent_calls"):
        source = s.digest.split(":")[0] if s.digest else "unknown"
        counts[source] += 1
    out = [
        {
            "source": src,
            "in_use": n > 0,
            "count": n,
            "off": "remove the post-tool hook call" if src == "hook" else "remove the importer",
        }
        for src, n in sorted(counts.items())
    ]
    out += [
        {"source": name, "in_use": False, "unavailable": True, "off": why}
        for name, why in sorted(NOT_COLLECTED.items())
        if name not in counts
    ]
    return out


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
