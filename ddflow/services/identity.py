"""Who is calling: the identity resolver the CLI, the typed api layer, the hooks and
onboarding share, and the B190 lease re-homing.

`resolve(root, cfg, declared)` answers `AgentId(id, source)` instead of each caller building
the log's agent from its own copy of the precedence: an explicit declaration, then
`DDFLOW_AGENT` (only while the config value is just its default), then `[agent].id`, then the
tree-derived default. `open_log` is the shared "resolve, then open the log as that agent"
step, and `bind` writes an identity that differs from the config's back into it, with the
layer that won, so `config --explain` names it. The MCP surface asks it too, through `api.identity`.

Carrying this clone's leases across the B190 identity upgrade.

B190 gave derived ids a per-clone suffix: `{host}-{tree}` became `{host}-{tree}-{hex6}`.
A lease claimed before that kept the bare id as its holder, and every holder comparison
afterwards -- renew, claim, complete, merge, the gate lease-keeper, the scheduler -- read
it as a stranger's: heartbeat said "no lease held" and a re-claim was refused as held by
someone else, on the agent's own work.

Rather than teach eleven comparisons an alias, the lease MOVES, once, on the record: a
release by the bare id and an acquisition by the suffixed one, carrying the claim's
worktree, branch, globs and resources. Release first: an acquisition over a live lease
of another holder is a contest (B191), which is exactly what this is not.

A bare id is not proof of THIS clone: two clones with the same hostname and directory
name derived the same bare id -- the collision B190 ended -- and their claims sit in one
union-merged shard. So a lease moves only with local evidence that it is ours:

* it was acquired BEFORE this clone had a suffix (after that, this clone derives the
  suffixed id, so a later bare-id claim is another clone's), and
* its worktree is registered in THIS clone's git and checked out on the lease's
  branch. Stored paths are relative, so the other clone records the same path; only
  this clone's own worktree list can say the tree is here.

A lease with no worktree carries no such evidence and stays where it is: re-claim it
with `--agent <bare id>` (`as_agent` over MCP), the workaround B205 was filed with.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ..adapters.env import DEFAULT as ENV
from ..config import Config
from ..core.model import Lease, State, fold
from ..infra import worktree as W
from ..infra.log import EventLog, bare_agent_id, clone_suffix_since, default_agent_id
from . import leases as L
from . import shardmarks  # noqa: F401  (registers the shard-mark append hook, Ba24a8d32ee)


class AgentId(NamedTuple):
    """An identity and WHICH LAYER produced it: explicit, env, config or derived (the CLI
    adds ddflow_identify for a harness's declaration)."""

    id: str
    source: str


#: The environment variable a client presents its token in: an argument would sit in `ps`.
TOKEN_ENV = "DDFLOW_TOKEN"


@dataclass(frozen=True)
class Credential:
    """What a verified token proved (`services/auth.verify`): the caller's name and role, and
    whether that role is the operator's, decided against the role table at verification."""

    name: str
    role: str
    token_id: str
    operator: bool = False


_CREDENTIAL: ContextVar[Credential | None] = ContextVar("ddflow_credential", default=None)
_PROOF_REQUIRED: ContextVar[bool] = ContextVar("ddflow_proof_required", default=False)


def token_presented() -> bool:
    """Whether this process was handed a token to prove, read through the Env port."""
    return bool(ENV.get(TOKEN_ENV))


def credential() -> Credential | None:
    """The token this call proved, if it did."""
    return _CREDENTIAL.get()


def bind_credential(proof: Credential) -> None:
    """Hold ``proof`` for the rest of this CLI call (a server uses `with_credential`)."""
    _CREDENTIAL.set(proof)


def require_proof() -> None:
    """`[agent].require_operator_token` for this call: no marker is no longer a person."""
    _PROOF_REQUIRED.set(True)


@contextmanager
def with_credential(proof: Credential) -> Iterator[None]:
    """Hold ``proof`` for the calls made inside: one request of a server."""
    token, demand = _CREDENTIAL.set(proof), _PROOF_REQUIRED.set(False)
    try:
        yield
    finally:
        _CREDENTIAL.reset(token)
        _PROOF_REQUIRED.reset(demand)


def resolve(root: Path | str, cfg: Config | None = None, declared: str = "") -> AgentId:
    """The identity a write will carry, and WHICH LAYER produced it.

    A verified token comes first and answers its own name: it is proof, the rest are claims.
    Then four layers: an explicit declaration (`declared`: `--agent`, `as_agent`, a harness's
    `ddflow_identify`) first; then `[agent].id` when a config layer sets it, which `DDFLOW_AGENT`
    does not override (the environment applies only while no config layer sets the key, by
    PROVENANCE: a file that sets it to "" still counts as setting it); then `DDFLOW_AGENT`; then
    the tree-derived default. ``declared`` is what the
    caller EXPLICITLY asked for and "" for nothing: passing a resolved value back in makes it
    look explicit, which is how `config --explain` once blamed the environment for a variable
    nobody had set.

    The layer is returned rather than inferred, because inferring it by comparing the result
    against each candidate is wrong whenever two candidates agree: with `DDFLOW_AGENT` unset
    and no `[agent].id`, the derived name differs from `cfg.agent.id` (`""`), so a
    value-comparison recorded the source as `env`. ``cfg`` is optional so callers below the
    config layer can still ask.

    The typed MCP path once called `EventLog(repo, "")`, which falls straight to the derived
    default and read neither the env var nor the config, so one connection's `ddflow_claim`
    and `ddflow_update` wrote into different shards (B88). One encoding, asked by every
    surface, is the fix.
    """
    if (proof := _CREDENTIAL.get()) is not None:
        return AgentId(proof.name, "token")  # a proof outranks every declaration, `as_agent` too
    if declared:
        return AgentId(declared, "explicit")
    env = os.environ.get("DDFLOW_AGENT", "")
    if cfg is not None:
        if env and getattr(cfg, "sources", {}).get("agent.id", "default") == "default":
            return AgentId(env, "env")
        if getattr(getattr(cfg, "agent", None), "id", ""):
            return AgentId(cfg.agent.id, "config")
    elif env:
        return AgentId(env, "env")
    return AgentId(default_agent_id(root), "derived")


#: Environment variables an agent harness sets in the shells it runs, so a command it
#: runs is known not to come from a person at their own terminal. Only the ones known
#: for certain: Claude Code exports CLAUDECODE=1. Another harness is recognised by
#: `--agent` or `DDFLOW_AGENT`, which ddflow's own setup has it pass.
HARNESS_MARKERS = ("CLAUDECODE",)


_MCP_CALL: ContextVar[bool] = ContextVar("ddflow_mcp_call", default=False)


@contextmanager
def via_mcp() -> Iterator[None]:
    """Mark the calls made inside as arriving over MCP. The MCP dispatch wraps every tool
    call in it, so who is an agent comes from HOW the call arrived, not from variables a
    server process may not have (fix-agent-identity-over-mcp-in-api)."""
    token = _MCP_CALL.set(True)
    try:
        yield
    finally:
        _MCP_CALL.reset(token)


def _marker(requested_agent: str) -> str:
    if requested_agent:
        return f"--agent {requested_agent}"
    if os.environ.get("DDFLOW_AGENT"):
        return f"DDFLOW_AGENT={os.environ['DDFLOW_AGENT']}"
    for var in HARNESS_MARKERS:
        if os.environ.get(var):
            return f"{var} is set (an agent harness's shell)"
    # Last: the more specific marker names the agent; this is the one that cannot be unset.
    return "the MCP surface" if _MCP_CALL.get() else ""


def agent_marker(requested_agent: str = "") -> str:
    """Why this invocation is not the operator's, or ``""`` when nothing says so.

    A verified token decides first, whatever else is set: its role is the operator's or it is
    not. Without one: an explicit `--agent`, `DDFLOW_AGENT`, or a harness's own marker. A person
    at their own terminal sets none of them; an agent that unsets all three is the shell edit
    the decision accepts it cannot stop, unless `[agent].require_operator_token` asks for proof.
    """
    if (proof := _CREDENTIAL.get()) is not None:
        return "" if proof.operator else f"token {proof.name}, role {proof.role}"
    if why := _marker(requested_agent):
        return why
    return "no operator token presented" if _PROOF_REQUIRED.get() else ""


def lacks_only_proof(requested_agent: str = "") -> bool:
    """`agent_marker` refuses this call for the missing operator token alone: no marker says
    it is an agent's, so it may mint the first operator token."""
    return _CREDENTIAL.get() is None and _PROOF_REQUIRED.get() and not _marker(requested_agent)


def is_agent(requested_agent: str = "", via_mcp: bool = False) -> str:
    """Why this call is an agent's, or "" for a person at a terminal. The MCP surface is
    always an agent unless a token says otherwise; a CLI call is one under `--agent`,
    `DDFLOW_AGENT` or a harness's marker (the rule ``reviewers approve`` and the export
    enable use)."""
    if via_mcp and _CREDENTIAL.get() is None:
        return "the MCP surface"
    return agent_marker(requested_agent)  # marker: MCP call too


def bind(cfg: Config, who: AgentId) -> None:
    """Record the resolved identity on ``cfg`` (value and the layer that won), as one
    invocation's fact that every surface must agree on."""
    if who.id != cfg.agent.id:
        cfg.agent.id = who.id
        cfg.sources["agent.id"] = who.source


def open_log(
    root: Path | str,
    cfg: Config,
    declared: str = "",
    *,
    lock_timeout_s: float | None = None,
    bind_cfg: bool = False,
) -> tuple[EventLog, AgentId]:
    """Resolve the identity and open the log as that agent; also returns the answer. ``bind_cfg`` also writes the
    answer back into ``cfg`` (see :func:`bind`); ``lock_timeout_s`` defaults to
    ``[lease].acquire_timeout_s`` (a hook passes a shorter wait)."""
    who = resolve(root, cfg, declared)
    if bind_cfg:
        bind(cfg, who)
    log = EventLog(
        root,
        who.id,
        lock_timeout_s=cfg.lease.acquire_timeout_s if lock_timeout_s is None else lock_timeout_s,
        log_cfg=cfg.log,
    )
    return log, who


def _pre_upgrade(cfg: Config, st: State, bare: str, since: float) -> list[str]:
    live = st.active_leases(time.time(), cfg.lease.grace_s)
    return sorted(
        i
        for i, lease in live.items()
        if lease.holder == bare
        and lease.acquired_at < since
        and lease.worktree
        and lease.branch
        and not st.items[i].removed
        and not st.items[i].lease_contest
    )


def _tree_is_here(root: Path, lease: Lease, trees: list[dict[str, str]]) -> bool:
    path = W.load_path(root, lease.worktree).resolve()
    return any(
        Path(t.get("worktree", "")).resolve() == path
        and t.get("branch") == f"refs/heads/{lease.branch}"
        for t in trees
    )


def rehome_pre_upgrade_leases(log: EventLog, cfg: Config, st: State, layer: str) -> list[str]:
    """Move this clone's pre-upgrade leases to its suffixed id. Returns the item ids moved.

    Only for a DERIVED identity: an explicit, env or config name is never suffixed, so
    it has no bare predecessor. Checked against the fold the caller already has, so the
    common case -- nothing to move -- costs one pass over the live leases and no lock.
    """
    if layer != "derived":
        return []
    me = log.agent_id
    bare = bare_agent_id(log.root)
    since = clone_suffix_since(log.root)
    if me == bare or not since or not _pre_upgrade(cfg, st, bare, since):
        return []
    with log.transaction():
        # Decided again under the lock: a sibling process may have moved them already.
        st = fold(log.read_all(), strict=False)
        trees = W.list_worktrees(log.root)
        moved = [
            i
            for i in _pre_upgrade(cfg, st, bare, since)
            if _tree_is_here(log.root, st.items[i].lease, trees)  # type: ignore[arg-type]
        ]
        now = time.time()
        for item_id in moved:
            it = st.items[item_id]
            lease = it.lease
            assert lease is not None
            why = f"re-homed from {bare} to {me}: the B190 per-clone id upgrade"
            L.transfer(log, item_id, lease, to=me, note=why, kind=it.kind, now=now)
    # Outside the lock, as `leases.release` does: the remote claim ref moves with the
    # lease, or it kept naming the bare id and the new holder could not renew it
    # (Bd45d1ad60e). Best effort: a ref not moved lapses at its expiry.
    for item_id in moved:
        L.settle_remote(log, cfg, item_id, dropped=[bare], kept=me)
    return moved
