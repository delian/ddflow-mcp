"""Shared imports and leaf constants the MCP engine's submodules read.

`ddflow/surfaces/mcp.py` was one ~80 kB module; it is now the `ddflow/surfaces/mcp/`
package. This module holds what several submodules need and none should import from each
other for: the registry constant bindings (`AS_AGENT`), the deferred api accessors (an
eager import there would pull the api layer -- and its jinja2 -- into
`import ddflow.surfaces.mcp`, which `scripts/bump.sh` runs under a bare interpreter), the
`RESOURCES` a client can read, the per-call `_meta` helpers and the machinery-free
constants. Nothing here imports a sibling submodule; that would be a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddflow.core import agentname as AN

from ...config import Config
from .. import registry as _REGISTRY
from ..mcp_protocol import META_AGENT, MODERN_PROTOCOLS
from ..tools._common import _api

#: The per-CALL identity override every tool accepts except `ddflow_identify` itself.
#:
#: `ddflow_identify` declares identity per CONNECTION, which covers several agents that
#: each spawn their own server. It does not cover the configuration Claude Code actually
#: runs: subagents dispatched by one session share that session's MCP connection, so a
#: subagent that called `ddflow_identify` would re-identify its PARENT and every sibling
#: mid-flight. Their claims then share one holder -- and the glob-conflict check skips a
#: holder's own leases (`leases.acquire`), so two subagents claiming overlapping files
#: are both granted. The CLI has always had the per-call form (`--agent`); this is the
#: same thing on the other surface.
#: The skew-guard override (decision D-upgrade-skew-guard): the REASON an agent was told by the
#: operator to let this older ddflow write anyway. Accepted by every tool, stripped before the
#: tool sees its arguments, and deliberately NOT in any schema -- 90 copies of it would cost
#: more of every session's context than the rare refusal it answers; the refusal message and
#: the connection instructions name it where it is needed.
ALLOW_OLDER = "allow_older_version"
AS_AGENT = _REGISTRY.AS_AGENT
_AS_AGENT_SPEC = _REGISTRY.AS_AGENT_SPEC


def _waves_auto() -> Any:
    """`api.waves_auto`, imported on first use for the same reason as `_identity`."""
    from ...api import waves_auto  # deferred: the api package reaches jinja2

    return waves_auto


def _identity() -> Any:
    """`api.identity`, imported on first use: a module-level import would pull the whole api
    layer (and its jinja2) into `import ddflow.surfaces.mcp`, which `scripts/bump.sh` runs
    under a bare interpreter."""
    from ...api import identity  # deferred: the api package reaches jinja2

    return identity


def _surf() -> Any:
    """`api.surf_mcp`, imported on first use for the same reason as `_identity`: it reaches
    the template engine, and everything the engine reads from the domain layers goes
    through it."""
    from ...api import surf_mcp  # deferred: reaches jinja2 (scripts/bump.sh has none)

    return surf_mcp


def _default_agent(repo: Path) -> tuple[str, str]:
    """(identity, where it came from) for a connection that declared none.

    The SOURCE matters as much as the name. "you are `alpha`, from DDFLOW_AGENT" and
    "you are `alpha`, because that is this directory's name" call for different
    reactions: the first was set deliberately by whatever spawned you, the second is a
    guess that every sibling in this tree will make identically.
    """
    # The EFFECTIVE default, not the tree-derived one. Reporting the tree name while
    # `DDFLOW_AGENT` was set made `ddflow_identify` misreport the single thing it
    # exists to make visible.

    try:
        cfg = Config.load(repo)
    except Exception:
        cfg = None
    who, layer = _identity().resolve(repo, cfg)
    return who, {
        "env": "from DDFLOW_AGENT",
        "config": "from [agent].id in config",
        "derived": "derived from the working tree",
        "explicit": "declared",
    }[layer]


@dataclass(frozen=True)
class Resource:
    """One MCP resource: what `resources/list` says of it and how `resources/read` serves it.

    Declared once, in `RESOURCES`: the URI list and the read map used to be two literals that
    had to agree, and a dead entry in one that the other shadowed is how a second path hides.
    Every resource is read through the api, like every tool (B97)."""

    uri: str
    name: str
    description: str
    read: Callable[[Path], str]
    mime: str = "text/markdown"

    def listing(self) -> dict[str, str]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime,
        }


def _rendered(show: str) -> Callable[[Path], str]:
    return lambda repo: _api().render(repo, show=show).data["text"]


#: The resources a client can read, in the order `resources/list` shows them.
RESOURCES: tuple[Resource, ...] = (
    Resource(
        "ddflow://board",
        "Work queue",
        "The full queue with the critical path.",
        lambda repo: _api().board(repo).data["text"],
    ),
    Resource(
        "ddflow://brief",
        "Session brief",
        "Budgeted session-start pack.",
        lambda repo: _api().brief(repo).data["text"],
    ),
    Resource("ddflow://lessons", "Lessons", "Everything learned so far.", _rendered("lessons")),
    Resource(
        "ddflow://lessons-summary",
        "Lessons summary",
        "Every live lesson in one paragraph, by tag.",
        _rendered("lessons-summary"),
    ),
    Resource(
        "ddflow://research",
        "Research log",
        "Findings with verdicts and probes.",
        _rendered("research"),
    ),
    Resource(
        "ddflow://bugs",
        "Bugs",
        "Every bug with its item, its regression tests and its lesson.",
        _rendered("bugs"),
    ),
    Resource(
        "ddflow://decisions",
        "Decisions",
        "The architectural decisions in force.",
        _rendered("decisions"),
    ),
    Resource(
        "ddflow://sessions",
        "Sessions",
        "The sessions, when they ran and what they were for.",
        _rendered("sessions"),
    ),
)
RESOURCE_BY_URI: dict[str, Resource] = {r.uri: r for r in RESOURCES}


def _meta_agent(params: dict[str, Any]) -> tuple[str, str]:
    """(the agent a stateless request names in its `_meta`, "" for none; why it cannot
    be used, "" when it can)."""
    meta = params.get("_meta")
    want = meta.get(META_AGENT, "") if isinstance(meta, dict) else ""
    if isinstance(want, str) and (not want.strip() or AN.is_valid(want.strip())):
        return want.strip(), ""
    return "", (f"_meta {META_AGENT!r} = {want!r} is not a usable agent name: {AN.USABLE}")


def _modern_identify_note(agent: str) -> str:
    """What `ddflow_identify` answers on a stateless request: nothing is declared."""
    who = agent or "the tree-derived default"
    return (
        f"not declared: on a {MODERN_PROTOCOLS[0]} request identity is per request, so "
        f"nothing persists for the connection (D-mcp-identity-per-call). Name yourself on "
        f"EVERY call: the `{AS_AGENT}` argument, or {META_AGENT!r} in params._meta "
        f"({AS_AGENT} wins when both are given). A request that names nobody is "
        f"attributed to the tree-derived default. This request: {who!r}."
    )


#: The look interval when `[mcp].stale_check_every_s` cannot be read (the knob's default).
_STALE_CHECK_EVERY_S = 60.0


#: How many calls try to build the first-call session start before the connection gives up.
_FIRST_CALL_TRIES = 3


#: Keys a tool's payload carries only when the operation produced them.
_OPTIONAL_KEYS = ("export_refresh", "ci", "progress", "guidance", "glob_warnings")


#: The first protocol revision with `outputSchema` and `structuredContent`.
_STRUCTURED_SINCE = "2025-06-18"


#: Tools that can run for minutes -- a gate command, a reviewer, a wait. A real stdio
#: server runs each in a worker PROCESS so the loop keeps answering: a 534 s unit_tests
#: run once timed out every other call (B5f209cb092); a reviewer that crashes or eats
#: the machine takes its worker down, not the server and the session's tools with it
#: (B55e649ca6e); and the worker sits in its own session, so a client that times out or
#: disconnects does not lose the run -- it finishes and records its outcome (B9abc247444).
#: A process, not a thread: the event log's caches are process-global and unlocked.
OFFLOADED = frozenset(
    {
        "ddflow_bisect",
        "ddflow_ci",
        "ddflow_gate_run",
        "ddflow_gate_verify",
        "ddflow_review",
        "ddflow_verify",
        "ddflow_wait",
        "ddflow_watch",
    }
)


def _progress_token(params: dict[str, Any]) -> str | int | float | None:
    """The `_meta.progressToken` of a call: MCP says a string or a number, and it is a dict
    key here, so anything else (a list from a non-conformant client) is no token."""
    meta = params.get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    if isinstance(token, bool) or not isinstance(token, (str, int, float)):
        return None
    return token
