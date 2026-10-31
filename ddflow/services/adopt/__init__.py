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

from ..mcpconfig import (  # noqa: F401 -- re-exported: their home was here
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
from ._adopt import adopt  # noqa: F401
from ._aider import AIDER_READS  # noqa: F401
from ._commands import AGENT_COMMANDS, MANAGED_MARK  # noqa: F401
from ._docs import (  # noqa: F401
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
from ._native import adopt_notes  # noqa: F401
from ._refused import Refused  # noqa: F401
from ._rules import (  # noqa: F401
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
from ._scaffold import (  # noqa: F401
    DDFLOW_GITIGNORE,
    GITIGNORE_REGION,
    UNION_MERGE_LINE,
    init_files,
    starter_config,
    write_ddflow_gitignore,
)
from ._targets import (  # noqa: F401
    AGENT_TARGETS,
    FORM_AIDER,
    FORM_BLOCK,
    FORM_WHOLE,
    NATIVE_RULES,
    NativeRule,
    AgentTarget,
)
from ._upsert import local_edits_path  # noqa: F401

__all__ = [
    # mcpconfig re-exports
    "SHAPE_COPILOT",
    "SHAPE_MCP_DOT_SERVERS",
    "SHAPE_MCP_SERVERS",
    "SHAPE_NONE",
    "SHAPE_OPENCODE",
    "SHAPE_SERVERS",
    "SHAPE_TOML",
    "UnplaceableConfig",
    "get_server",
    "get_servers",
    "place_server",
    "server_entry_for",
    # targets
    "AgentTarget",
    "AGENT_TARGETS",
    "FORM_WHOLE",
    "FORM_BLOCK",
    "FORM_AIDER",
    "NativeRule",
    "NATIVE_RULES",
    # rules block + status
    "BLOCK",
    "LEGACY_BEGIN",
    "LEGACY_END",
    "RULES_FILES",
    "MISSING",
    "NO_BLOCK",
    "STALE",
    "CURRENT",
    "DUPLICATE",
    "NOT_BINDING",
    "RulesState",
    "native_rule_text",
    "adopted_agents",
    "has_been_adopted",
    "block_body",
    "has_legacy_block",
    "block_marker",
    "block_spans",
    "block_count",
    "project_body",
    "project_section",
    "block_newer",
    "block_version",
    "block_edited",
    "rules_status",
    # scaffolding
    "starter_config",
    "DDFLOW_GITIGNORE",
    "UNION_MERGE_LINE",
    "GITIGNORE_REGION",
    "write_ddflow_gitignore",
    "init_files",
    # docs
    "DOC_CURRENT",
    "DOC_STALE",
    "DOC_LEGACY",
    "DOC_EDITED",
    "DOC_NEWER",
    "doc_state",
    "driver_states",
    "driver_drift",
    "local_edits_path",
    "refresh_docs",
    "adopt",
    # commands
    "AGENT_COMMANDS",
    "MANAGED_MARK",
    # claude extras / native
    "adopt_notes",
    "AIDER_READS",
    # launch
    "MCP_MODULE",
    "DIST_NAME",
    "Refused",
]
