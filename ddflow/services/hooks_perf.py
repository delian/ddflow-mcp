"""The perf post-tool hook (PM-agent-calls).

Runs after every worker tool call the harness already wraps, and appends ONE count into the
perf store's local ring -- the tool-name CLASS, ok/error and duration when given, keyed by
agent, lane and item. It never appends the arguments, the output, user paths or a secret.
"""

from __future__ import annotations


def record_tool_call(repo, agent, lane, item, tool_class, ok, duration_s=None) -> None:
    """The post-tool entry the harness hook calls. ``duration_s=None`` means the harness gave
    no duration (the store records the count 1, never a fabricated 0)."""
    from .perf.agentcalls import ToolCall, record

    record(repo, "hook", ToolCall(agent, lane, item, tool_class, ok, duration_s))


__all__ = ["record_tool_call"]
