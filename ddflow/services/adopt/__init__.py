"""`ddflow adopt` — install the workflow into any project, for any agent.

Adoption has to be one command, because a multi-step install is a step that gets
skipped. It writes four things and nothing else:

1. ``.ddflow/`` — config, gate definitions, the (empty) event log, the gitignore that
   keeps the derived index out of git and the log IN it;
2. ``docs/ddflow/drivers/`` — the canonical driver plus the delta for each agent you
   asked for;
3. an **AGENTS.md** section, because that is the one instructions file more than thirty
   agents read (OpenAI Codex, Copilot, Cursor, Gemini CLI, Jules, Aider, Zed, Windsurf,
   Devin…). Claude Code reads ``CLAUDE.md``, so a pointer is written there too;
4. the MCP registration for each requested agent, in that agent's own config location.

Everything it writes is idempotent and marked with a managed-block comment, so re-running
after an upgrade updates the block and leaves your own prose alone.

This module was one file (`services/adopt.py`) and became a package so its parts could
sit beside each other instead of in one 75 kB module. Its public names are still reached
here, unchanged.
"""

from __future__ import annotations

from ..mcpconfig import (
    SHAPE_COPILOT,
    SHAPE_MCP_DOT_SERVERS,
    SHAPE_MCP_SERVERS,
    SHAPE_NONE,
    SHAPE_OPENCODE,
    SHAPE_SERVERS,
    SHAPE_TOML,
    UnplaceableConfig,
    get_server,
    get_servers,
    place_server,
    server_entry_for,
)
from ._adopt import adopt
from ._aider import AIDER_READS
from ._commands import AGENT_COMMANDS, MANAGED_MARK
from ._docs import (
    DOC_CURRENT,
    DOC_EDITED,
    DOC_LEGACY,
    DOC_NEWER,
    DOC_STALE,
    doc_state,
    driver_drift,
    driver_states,
    refresh_docs,
)
from ._launch import (  # noqa: F401
    DIST_NAME,
    MCP_MODULE,
    _package_parent,
)
from ._native import adopt_notes
from ._refused import Refused
from ._rules import (
    BLOCK,
    CURRENT,
    DUPLICATE,
    LEGACY_BEGIN,
    LEGACY_END,
    MISSING,
    NO_BLOCK,
    NOT_BINDING,
    RULES_FILES,
    STALE,
    RulesState,
    adopted_agents,
    block_body,
    block_count,
    block_edited,
    block_marker,
    block_newer,
    block_spans,
    block_version,
    has_been_adopted,
    has_legacy_block,
    native_rule_text,
    project_body,
    project_section,
    rules_status,
)
from ._scaffold import (
    DDFLOW_GITIGNORE,
    GITIGNORE_REGION,
    UNION_MERGE_LINE,
    init_files,
    starter_config,
    write_ddflow_gitignore,
)
from ._targets import (
    AGENT_TARGETS,
    FORM_AIDER,
    FORM_BLOCK,
    FORM_WHOLE,
    NATIVE_RULES,
    AgentTarget,
    NativeRule,
)
from ._upsert import local_edits_path

__all__ = [
    "AGENT_COMMANDS",
    "AGENT_TARGETS",
    "AIDER_READS",
    "BLOCK",
    "CURRENT",
    "DDFLOW_GITIGNORE",
    "DIST_NAME",
    "DOC_CURRENT",
    "DOC_EDITED",
    "DOC_LEGACY",
    "DOC_NEWER",
    "DOC_STALE",
    "DUPLICATE",
    "FORM_AIDER",
    "FORM_BLOCK",
    "FORM_WHOLE",
    "GITIGNORE_REGION",
    "LEGACY_BEGIN",
    "LEGACY_END",
    "MANAGED_MARK",
    "MCP_MODULE",
    "MISSING",
    "NATIVE_RULES",
    "NOT_BINDING",
    "NO_BLOCK",
    "RULES_FILES",
    "SHAPE_COPILOT",
    "SHAPE_MCP_DOT_SERVERS",
    "SHAPE_MCP_SERVERS",
    "SHAPE_NONE",
    "SHAPE_OPENCODE",
    "SHAPE_SERVERS",
    "SHAPE_TOML",
    "STALE",
    "UNION_MERGE_LINE",
    "AgentTarget",
    "NativeRule",
    "Refused",
    "RulesState",
    "UnplaceableConfig",
    "adopt",
    "adopt_notes",
    "adopted_agents",
    "block_body",
    "block_count",
    "block_edited",
    "block_marker",
    "block_newer",
    "block_spans",
    "block_version",
    "doc_state",
    "driver_drift",
    "driver_states",
    "get_server",
    "get_servers",
    "has_been_adopted",
    "has_legacy_block",
    "init_files",
    "local_edits_path",
    "native_rule_text",
    "place_server",
    "project_body",
    "project_section",
    "refresh_docs",
    "rules_status",
    "server_entry_for",
    "starter_config",
    "write_ddflow_gitignore",
]
