"""Onboarding stage 1: approving the project's MCP servers, which fails silently when wrong.

A project `.mcp.json` server Claude Code was never told to trust does not start, and
nothing errors: interactive sessions show an approval prompt, unattended ones cannot, and
a server that never starts is the queue itself. The approval is recorded as
``enabledMcpjsonServers`` in the COMMITTED ``.claude/settings.json``, so every clone and
worktree session inherits it.

Only project files are written. User settings are never edited: per Claude Code's own
documentation (code.claude.com/docs/en/mcp), a ``disabledMcpjsonServers`` entry in ANY
settings file rejects a server in every permission mode, so one is REPORTED here, never
overridden.

(B-onboard-harness also copied a sibling project's machine-local config and wrote a shell
wrapper for a PYTHONPATH-pinned entry. Nothing called either and no planned work would, so
B-uni-dead-code removed them; the onboard prompt describes both steps for the operator.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adopt import SHAPE_MCP_SERVERS, Refused, get_servers
from .claudehooks import SettingsError, _read, _write

#: Claude Code's COMMITTED project settings. Deliberately not `settings.local.json`:
#: approval has to reach every clone and every worktree session, and an untracked local
#: file is checked out by nobody.
SETTINGS_REL = ".claude/settings.json"
#: The project MCP file whose servers that setting gates.
MCP_REL = ".mcp.json"
ENABLED_KEY = "enabledMcpjsonServers"
DISABLED_KEY = "disabledMcpjsonServers"


class HarnessError(ValueError):
    """A harness file exists and cannot be read as what it must be; nothing was written."""


def project_servers(repo: Path, *, mcp_rel: str = MCP_REL) -> dict[str, Any]:
    """Every MCP server registered in the project, by name.

    Read with `adopt.get_servers`, the same shape vocabulary the writer places them
    with: a second reader that guessed `mcpServers` would drift from the one that wrote
    the file, and a wrong key is valid JSON that is silently ignored.
    """
    path = Path(repo) / mcp_rel
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise HarnessError(f"{mcp_rel} could not be read ({exc}); fix it by hand") from exc
    if not isinstance(data, dict):
        raise HarnessError(f"{mcp_rel} is not a JSON object; fix it by hand")
    return get_servers(data, SHAPE_MCP_SERVERS)


def registered_entry(repo: Path, name: str = "ddflow", *, mcp_rel: str = MCP_REL) -> Any:
    """The entry the harness will actually start for `name`, or None."""
    return project_servers(repo, mcp_rel=mcp_rel).get(name)


def enable_project_servers(
    repo: Path, *, mcp_rel: str = MCP_REL, settings_rel: str = SETTINGS_REL
) -> list[str]:
    """Approve every registered server in the committed project settings.

    Returns action lines; a server the operator put in `disabledMcpjsonServers` is a
    `Refused`, because that list "blocks it in every permission mode" and silently
    clearing it would reverse a decision this function has no mandate to touch.

    Raises `SettingsError` (from `claudehooks`) for a settings file that cannot be read
    or holds a non-list where these keys belong: rewriting such a file would drop
    whatever the operator was halfway through writing.
    """
    names = list(project_servers(repo, mcp_rel=mcp_rel))
    if not names:
        return [f"no servers registered in {mcp_rel}; nothing to approve"]
    path = Path(repo) / settings_rel
    data = _read(path)
    # `.get(KEY, [])`, NOT `.get(KEY) or []`: a falsy non-list (`{}`, `""`, `false`)
    # would be silently replaced by a list, rewriting exactly the malformed file the
    # type check below exists to leave alone (rubber_duck on 91c639e).
    enabled = data.get(ENABLED_KEY, [])
    denied = data.get(DISABLED_KEY, [])
    for key, value in ((ENABLED_KEY, enabled), (DISABLED_KEY, denied)):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{path}: `{key}` is not a list of names; not touching it")
    add = [n for n in names if n not in enabled and n not in denied]
    refused = [
        Refused(
            f"{n} is listed in {DISABLED_KEY} in {settings_rel}, which rejects it in every "
            f"permission mode; remove it there first if the operator wants it started"
        )
        for n in names
        if n in denied
    ]
    if add:
        data[ENABLED_KEY] = [*enabled, *add]
        _write(path, data)
        done = (
            f"approved {', '.join(add)} in {settings_rel} ({ENABLED_KEY}); Claude Code "
            f"still shows its one-time workspace-trust dialog in a folder it has not trusted"
        )
    elif refused:
        done = f"nothing added in {settings_rel}: every registered server is disabled there"
    else:
        done = f"every server in {mcp_rel} is already approved in {settings_rel}"
    return [done, *refused]
