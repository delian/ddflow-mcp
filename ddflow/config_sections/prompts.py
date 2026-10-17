"""The `[prompts]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("prompts")
@dataclass
class PromptsConfig:
    """Paths to prompt templates that replace the shipped ones.

    Empty means "use the project's `.ddflow/prompts/<name>.md` if present, else the
    packaged default". Set a path here to point somewhere else entirely -- a shared
    prompts repository, for instance.
    """

    review_system: str = knob(
        "",
        doc="Path to the reviewer's system prompt template, replacing the shipped one. `ddflow prompts eject` writes an editable copy into .ddflow/prompts/ to start from.",
    )
    review_user: str = knob(
        "",
        doc="Path to the per-chunk review message template. Variables: intent, context, diff, fence (the backtick fence that safely holds the diff), chunk_index, chunk_total.",
    )
    gate_instruction: str = knob(
        "",
        doc="Path to the template rendered when an agent asks what a gate requires. Variables: gate, item.",
    )
    session_brief_header: str = knob(
        "",
        doc="Path to the header template for `ddflow brief`.",
    )
    mcp_instructions: str = knob(
        "",
        doc="Path to the instruction block the MCP server hands the agent on connect — the workflow, the reporting duties and the companion tools it should use. This is the file to edit to change how the project works. `ddflow prompts eject mcp_instructions` writes an editable copy into .ddflow/prompts/.",
    )
    agent_brief: str = knob(
        "",
        doc="Path to the launch-brief template `ddflow brief --for-agent` renders for a subagent: sections split by `<!-- section: NAME -->` lines. Variables: agent, item, title, body, globs, needs, base, lease_s, heartbeat_s, decisions_text, lessons_text, note, extra. `ddflow prompts eject agent_brief` writes an editable copy.",
    )
    agent_report: str = knob(
        "",
        doc="Path to the completion-report template `ddflow report ITEM` renders: sections split by `<!-- section: NAME -->` lines. Variables: item, title, agent, state, landing, tests, gates, by_hand, overrides, filed, follow_ups, not_done (each already text). `ddflow prompts eject agent_report` writes an editable copy.",
    )
