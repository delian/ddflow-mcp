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

import tomllib
from pathlib import Path
from typing import Any

from ..infra.fsio import Unreadable, read_json
from .adopt import SHAPE_MCP_SERVERS, SHAPE_TOML, Refused, get_servers
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


def project_servers(
    repo: Path, *, mcp_rel: str = MCP_REL, shape: str = SHAPE_MCP_SERVERS
) -> dict[str, Any]:
    """Every MCP server registered in the project, by name.

    Read with `adopt.get_servers`, the same shape vocabulary the writer places them
    with: a second reader that guessed `mcpServers` would drift from the one that wrote
    the file, and a wrong key is valid JSON that is silently ignored.
    """
    path = Path(repo) / mcp_rel
    if not path.is_file():
        return {}
    data = read_json(path)
    if isinstance(data, Unreadable) and data.kind == "not-object":
        raise HarnessError(f"{mcp_rel} is not a JSON object; fix it by hand")
    if isinstance(data, Unreadable):
        raise HarnessError(f"{mcp_rel} could not be read ({data.detail}); fix it by hand")
    return get_servers(data, shape)


def registered_entry(repo: Path, name: str = "ddflow", *, mcp_rel: str = MCP_REL) -> Any:
    """The entry the harness will actually start for `name`, or None."""
    return project_servers(repo, mcp_rel=mcp_rel).get(name)


def enable_project_servers(
    repo: Path,
    *,
    mcp_rel: str = MCP_REL,
    settings_rel: str = SETTINGS_REL,
    enabled_key: str = ENABLED_KEY,
    disabled_key: str = DISABLED_KEY,
    shape: str = SHAPE_MCP_SERVERS,
) -> list[str]:
    """Approve every registered server in the committed project settings.

    Returns action lines; a server the operator put in `disabledMcpjsonServers` is a
    `Refused`, because that list "blocks it in every permission mode" and silently
    clearing it would reverse a decision this function has no mandate to touch.

    Raises `SettingsError` (from `claudehooks`) for a settings file that cannot be read
    or holds a non-list where these keys belong: rewriting such a file would drop
    whatever the operator was halfway through writing.
    """
    names = list(project_servers(repo, mcp_rel=mcp_rel, shape=shape))
    if not names:
        return [f"no servers registered in {mcp_rel}; nothing to approve"]
    path = Path(repo) / settings_rel
    data = _read(path)
    # `.get(KEY, [])`, NOT `.get(KEY) or []`: a falsy non-list (`{}`, `""`, `false`)
    # would be silently replaced by a list, rewriting exactly the malformed file the
    # type check below exists to leave alone (rubber_duck on 91c639e).
    enabled = data.get(enabled_key, [])
    denied = data.get(disabled_key, [])
    for key, value in ((enabled_key, enabled), (disabled_key, denied)):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{path}: `{key}` is not a list of names; not touching it")
    add = [n for n in names if n not in enabled and n not in denied]
    refused = [
        Refused(
            f"{n} is listed in {disabled_key} in {settings_rel}, which rejects it in every "
            f"permission mode; remove it there first if the operator wants it started"
        )
        for n in names
        if n in denied
    ]
    if add:
        data[enabled_key] = [*enabled, *add]
        _write(path, data)
        done = (
            f"approved {', '.join(add)} in {settings_rel} ({enabled_key}); the agent "
            f"may still show its one-time workspace-trust dialog in a folder it has not trusted"
        )
    elif refused:
        done = f"nothing added in {settings_rel}: every registered server is disabled there"
    else:
        done = f"every server in {mcp_rel} is already approved in {settings_rel}"
    return [done, *refused]


def mcp_registered(repo: Path, h: Any) -> bool | None:
    """Is a ``ddflow`` server in the agent's project MCP file? None when the file exists and
    cannot be read: unknown is not absent."""
    path = Path(repo) / h.mcp.path
    if not path.is_file():
        return False
    if h.mcp.shape == SHAPE_TOML:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # TOMLDecodeError and UnicodeDecodeError are ValueErrors
            return None
        servers = data.get("mcp_servers")
        return isinstance(servers, dict) and "ddflow" in servers
    data = read_json(path)
    if isinstance(data, Unreadable):
        return None
    return "ddflow" in get_servers(data, h.mcp.shape)


def _step(step: str, status: str, detail: str) -> dict[str, str]:
    return {"step": step, "status": status, "detail": detail}


def onboard_steps(repo: Path, h: Any, *, apply: bool) -> list[dict[str, str]]:
    """The onboarding steps for one agent, each read from its descriptor.

    ``status`` is ``done`` (nothing to do, or done now), ``todo`` (needed, and ``apply`` was
    not given), ``refused`` (the operator's own setting stands in the way), ``unknown`` (a
    file could not be read, or the descriptor does not say) or ``n/a`` (the agent has no such
    step). A step is never ``done`` on a guess.
    """
    steps: list[dict[str, str]] = []
    reg = mcp_registered(repo, h) if h.mcp.path else None
    if not h.mcp.path:
        steps.append(_step("mcp", "n/a", "the agent has no project MCP file; see its delta doc"))
    elif reg is None:
        steps.append(_step("mcp", "unknown", f"{h.mcp.path} cannot be read"))
    elif not reg:
        steps.append(
            _step("mcp", "todo", f"no ddflow server in {h.mcp.path}: ddflow adopt --agents {h.id}")
        )
    else:
        steps.append(_step("mcp", "done", f"ddflow is registered in {h.mcp.path}"))
    steps.append(_approval(repo, h, reg, apply))
    steps.append(_hooks(repo, h, apply))
    return steps


def _approval(repo: Path, h: Any, registered: bool | None, apply: bool) -> dict[str, str]:
    m = h.mcp
    if not m.approve_file:
        return _step("approve", "n/a", "the descriptor records no approval step for this agent")
    if not registered:
        return _step(
            "approve", "todo" if registered is False else "unknown", "register the server first"
        )
    try:
        if not apply:
            data = _read(Path(repo) / m.approve_file)
            on = "ddflow" in (data.get(m.approve_key) or [])
            return _step(
                "approve", "done" if on else "todo", f"{m.approve_key} in {m.approve_file}"
            )
        lines = enable_project_servers(
            repo,
            mcp_rel=m.path,
            settings_rel=m.approve_file,
            enabled_key=m.approve_key,
            disabled_key=m.deny_key or m.approve_key + "-denied",
            shape=m.shape,
        )
    except (SettingsError, HarnessError) as exc:
        return _step("approve", "unknown", str(exc))
    refused = [x for x in lines if isinstance(x, Refused)]
    return _step("approve", "refused" if refused else "done", "; ".join(map(str, lines)))


def _hooks(repo: Path, h: Any, apply: bool) -> dict[str, str]:
    from . import hookwriters as HW

    if h.hooks is None:
        return _step("hooks", "unknown", "hooks are NOT VERIFIED in the descriptor")
    why = HW.unsupported(h)
    if why:
        return _step("hooks", "n/a", why)
    try:
        if apply:
            return _step("hooks", "done", "; ".join(HW.apply(repo, h, "install")))
        missing = [s.event for s, on, _w in HW.state(repo, h) if on is not True]
    except (SettingsError, HW.Unsupported) as exc:
        return _step("hooks", "unknown", str(exc))
    return _step(
        "hooks",
        "todo" if missing else "done",
        f"{h.hooks.file}: {', '.join(missing) or 'installed'}",
    )
