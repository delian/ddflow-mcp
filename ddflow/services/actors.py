"""Who is acting, as a role with capabilities: the one resolver every auth and approval path asks.

An actor is an id, a role (operator, bot, agent, observer ... -- names in `[agent].roles`), a
model family and the capabilities its role holds. Nothing is decided per role in code: a
capability is a name a consumer asks for (``can(actor, "waive")``) and the `[agent].roles`
table says who holds it (decisions D-remote and D-shared-engines-r2 10). Serve auth, hub and
scoped tokens, the approvals engine and waiver approvers all call `resolve_actor` and `require`
rather than each branching on "is this an agent".

Where the role comes from, strongest first:

* ``credential``  a role a paired token proves (the caller verified it, this module trusts it);
* ``named``       `[agent].actors`, applied only to an id a credential proved: an id an agent
                  merely declares is a claim and is never looked up;
* ``terminal``    no `--agent`, no `DDFLOW_AGENT`, no harness marker, not MCP: a person
                  (`[agent].operator_role`). A shell edit by an agent that unsets its markers
                  is the gap `identity.agent_marker` documents, not one this closes;
* ``default``     everyone else (`[agent].default_role`).

An unknown role holds nothing: the table fails closed. The family comes from the same
`family_for` / `router_set` the review stack uses, so "is the reviewer independent of the
author" and "who may approve" read one map.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config, family_for, router_set
from .identity import is_agent

ANY = "*"


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    source: str  #: credential | named | terminal | default
    capabilities: frozenset[str]
    model: str = ""
    family: str = ""  #: "" = not recognised (never read as "different")
    families: tuple[str, ...] = ()  #: a router's whole set; empty for an ordinary model

    def can(self, capability: str) -> bool:
        return ANY in self.capabilities or capability in self.capabilities


def capabilities_of(cfg: Config, role: str) -> frozenset[str]:
    """What ``role`` holds in `[agent].roles`; nothing for a role the table does not name."""
    return frozenset(cfg.agent.roles.get(role, ()))


def _role(
    cfg: Config, agent_id: str, requested_agent: str, via_mcp: bool, credential_role: str, authenticated: bool
) -> tuple[str, str]:
    if credential_role:
        return credential_role, "credential"
    if authenticated and agent_id in cfg.agent.actors:
        return cfg.agent.actors[agent_id], "named"
    if not is_agent(requested_agent, via_mcp):
        return cfg.agent.operator_role, "terminal"
    return cfg.agent.default_role, "default"


def resolve_actor(  # noqa: PLR0913 -- one keyword per proof a surface can bring
    cfg: Config,
    agent_id: str,
    *,
    requested_agent: str = "",
    via_mcp: bool = False,
    credential_role: str = "",
    authenticated: bool = False,
    model: str = "",
) -> Actor:
    """The actor behind a call. ``credential_role`` is a role a token proved; ``authenticated``
    says the id itself was proved (so `[agent].actors` may name its role)."""
    role, source = _role(cfg, agent_id, requested_agent, via_mcp, credential_role, authenticated)
    members = router_set(model, cfg.agent.routers) if model else None
    return Actor(
        id=agent_id,
        role=role,
        source=source,
        capabilities=capabilities_of(cfg, role),
        model=model,
        family="" if members is not None else family_for(model, cfg.agent.families),
        families=tuple(members or ()),
    )


def require(actor: Actor, capability: str) -> str:
    """``""`` when ``actor`` holds ``capability``, else the reason it is refused, naming the
    role, where it came from and how to change the table."""
    if actor.can(capability):
        return ""
    return (
        f"{actor.id} acts as role '{actor.role}' ({actor.source}), which does not hold "
        f"capability '{capability}'. Roles and capabilities are the [agent].roles table "
        "(`ddflow config agent.roles`); widening a role is the operator's."
    )
