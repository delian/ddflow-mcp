"""MCP tool profiles: which tools `tools/list` advertises (MTD-profiles, Ddcde0f5b95).

The default is the ``lean`` core: the 9 hot single tools plus the six families a daily loop
needs (15 tools). ``families`` (all 15 families, 24 tools) and ``hot+families`` (the 21 most
called direct tools plus the families, 36) are opt-in, chosen by `[mcp].tools` or by the client
table. A profile hides schemas and never tools: every tool, every family action and every old
name stays callable, and `listChanged` stays false. The flat tiers (`core`, `standard`, `all`)
are `tiers.py`, unchanged.
"""

from __future__ import annotations

from .families import FAMILIES, HOT_SINGLES
from .family_schema import family_listing

PROFILES = ("lean", "families", "hot+families")

#: `[mcp].tools = auto` (the default) is the lean core unless the client table says otherwise.
AUTO = "auto"
DEFAULT_PROFILE = "lean"

_LEAN_FAMILIES = ("gate", "bug", "session", "task", "view", "memory")

#: The most-called direct tools `hot+families` adds beside the 9 hot singles (research section 2).
_HOT_EXTRA = (
    "gate_record gate_status gate_run gate_skip bug_found bug_fixed task_add session_prompt "
    "review recall research_add update"
).split()

#: `initialize.clientInfo.name` (lower case) -> profile, for `auto`. Empty: no client has been
#: measured to need more than the lean core, and the caps of the clients are not all confirmed
#: (research section 3); a client opts in with `[mcp].tools`.
CLIENT_PROFILES: dict[str, str] = {}


def _families(profile: str) -> tuple[str, ...]:
    names = [n for n, f in FAMILIES.items() if not f.single]
    if profile == "lean":
        return tuple(n for n in names if n.removeprefix("ddflow_") in _LEAN_FAMILIES)
    return tuple(names)


def direct_tools(profile: str) -> tuple[str, ...]:
    """The tools of `TOOLS` this profile lists under their own names."""
    if profile == "hot+families":
        return (*HOT_SINGLES, *("ddflow_" + n for n in _HOT_EXTRA))
    return HOT_SINGLES


def family_tools(profile: str) -> tuple[str, ...]:
    """The family tools this profile lists."""
    return _families(profile)


def advertised_names(profile: str) -> list[str]:
    """Every tool name `tools/list` carries under ``profile``, sorted."""
    return sorted((*direct_tools(profile), *family_tools(profile)))


def family_entries(profile: str) -> list[dict]:
    """The `tools/list` entries of this profile's family tools."""
    want = set(family_tools(profile))
    return [e for e in family_listing() if e["name"] in want]


def settle(configured: str, client_name: str = "") -> str:
    """The profile a connection runs: ``configured`` unless it is ``auto``, which is the client
    table's entry for ``client_name`` or the lean core."""
    if configured != AUTO:
        return configured
    return CLIENT_PROFILES.get((client_name or "").strip().lower(), DEFAULT_PROFILE)


def profile_note(profile: str, total: int = 0) -> str:
    """What a profile hides and how to widen it, for `ddflow_help` and the handshake."""
    n = len(advertised_names(profile))
    wider = {
        "lean": "`families`, `hot+families` or `all`",
        "families": "`hot+families` or `all`",
        "hot+families": "`all`",
    }[profile]
    return (
        f"This connection lists the `{profile}` profile ({n} tools"
        f"{f' of {total}' if total else ''}). The rest are NOT in `tools/list` but stay callable: "
        "every tool by its own name, and every family tool by `action` (`ddflow_gate "
        "action=record`); `ddflow_help` names them. To list more, set `[mcp].tools` to "
        f"{wider} in .ddflow/config.toml (or `DDFLOW_MCP_TOOLS`) and restart the server; the "
        "profile is read once at start."
    )
