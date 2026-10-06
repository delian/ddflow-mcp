"""The `[prompts]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class PromptsConfig:
    """Paths to prompt templates that replace the shipped ones.

    Empty means "use the project's `.ddflow/prompts/<name>.md` if present, else the
    packaged default". Set a path here to point somewhere else entirely -- a shared
    prompts repository, for instance.
    """

    review_system: str = ""
    review_user: str = ""
    gate_instruction: str = ""
    session_brief_header: str = ""
    mcp_instructions: str = ""


_doc(
    "prompts",
    "mcp_instructions",
    "Path to the instruction block the MCP server hands the agent on connect — the workflow, the reporting duties and the companion tools it should use. This is the file to edit to change how the project works. `ddflow prompts eject mcp_instructions` writes an editable copy into .ddflow/prompts/.",
)
_doc(
    "prompts",
    "review_system",
    "Path to the reviewer's system prompt template, replacing the shipped one. `ddflow prompts eject` writes an editable copy into .ddflow/prompts/ to start from.",
)
_doc(
    "prompts",
    "review_user",
    "Path to the per-chunk review message template. Variables: intent, context, diff, fence (the backtick fence that safely holds the diff), chunk_index, chunk_total.",
)
_doc(
    "prompts",
    "gate_instruction",
    "Path to the template rendered when an agent asks what a gate requires. Variables: gate, item.",
)
_doc("prompts", "session_brief_header", "Path to the header template for `ddflow brief`.")
