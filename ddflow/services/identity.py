"""Who is calling: the identity resolver the CLI, the typed api layer, the hooks and
onboarding share, and the B190 lease re-homing.

`resolve(root, cfg, declared)` answers `AgentId(id, source)` instead of each caller building
the log's agent from its own copy of the precedence: an explicit declaration, then
`DDFLOW_AGENT` (only while the config value is just its default), then `[agent].id`, then the
tree-derived default. `open_log` is the shared "resolve, then open the log as that agent"
step, and `bind` writes an identity that differs from the config's back into it, with the
layer that won, so `config --explain` names it. The MCP surface still resolves through
`infra.log` until B-uni-identity.3-resolver.4-mcp moves it here.

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

import time
from pathlib import Path
from typing import NamedTuple

from ..config import Config
from ..core.model import Lease, State, fold
from ..infra import worktree as W
from ..infra.log import EventLog, bare_agent_id, clone_suffix_since, resolve_agent_id
from . import leases as L


class AgentId(NamedTuple):
    """An identity and WHICH LAYER produced it: explicit, env, config or derived (the CLI
    adds ddflow_identify for a harness's declaration)."""

    id: str
    source: str


def resolve(root: Path | str, cfg: Config | None = None, declared: str = "") -> AgentId:
    """The identity a write will carry. ``declared`` is what the caller EXPLICITLY asked
    for (`--agent`, `as_agent`, a harness declaration) and "" for nothing: passing a
    resolved value back in makes it look explicit, which is how `config --explain` once
    blamed the environment for a variable nobody had set."""
    return AgentId(*resolve_agent_id(root, cfg, declared))


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
