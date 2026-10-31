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
here, unchanged, alongside every name the old module surfaced.
"""

from __future__ import annotations

# ruff: noqa: F401 -- this module is a re-export shim: every import below is public API.
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
from ._aider import (
    _AIDER_LISTED,
    AIDER_READS,
    _add_aider_read,
    _aider_lists_agents_md,
    _aider_read_values,
)
from ._commands import (
    AGENT_COMMANDS,
    MANAGED_MARK,
    _command_region,
    _head_stamp,
    _install_prompt_hooks,
    _refresh_head,
    _region_body,
    _split_front_matter,
    _update_managed,
    _write_command,
)
from ._docs import (
    DOC_CURRENT,
    DOC_EDITED,
    DOC_LEGACY,
    DOC_NEWER,
    DOC_STALE,
    _backup_docs,
    _driver_pairs,
    _driver_region,
    _write_driver,
    doc_state,
    driver_drift,
    driver_states,
    refresh_docs,
)
from ._launch import (
    _DOCS_HINT,
    DIST_NAME,
    MCP_MODULE,
    _installed_entry,
    _installed_from_index,
    _launch_entry,
    _package_parent,
    _python,
    _register_mcp,
    _running_from_source,
)
from ._native import (
    _claude_extras,
    _write_native_rule,
    adopt_notes,
)
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
    _block_state,
    _stamped,
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
    _WORKTREES_IGNORED,
    DDFLOW_GITIGNORE,
    GITIGNORE_REGION,
    UNION_MERGE_LINE,
    _append_once,
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
from ._upsert import (
    _begin_lines,
    _collapse_blocks,
    _section_body,
    _upsert_block,
    local_edits_path,
)

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
