"""What every operation in this layer needs before it can answer anything.

Separate from `__init__` so a family module can import it without importing the
package that imports the family module. One file, one import edge, no cycle.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog, resolve_agent_id


def _load(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
    """Config FIRST, then the log — because the identity depends on the config.

    This used to be `EventLog(repo, agent)` with an empty `agent`, which falls to
    `default_agent_id()` and reads neither `DDFLOW_AGENT` nor `[agent].id`. The argv
    path resolved all four layers; this one resolved one. Same connection, same call,
    two identities, two shards.
    """
    cfg = Config.load(repo)
    # Resolved AND written back, exactly as `surfaces/context.Ctx` does. Resolving without
    # writing back is a third copy of the same drift: `config --explain` reported
    # `agent.id = ""  [default]` from this path while the argv path reported the value the
    # env var actually set. The identity is a fact about this invocation, and a config
    # object that does not carry it is a config object two surfaces disagree about.
    resolved, layer = resolve_agent_id(repo, cfg, agent)
    if resolved != cfg.agent.id:
        cfg.agent.id = resolved
        # The layer that actually WON, not a guess from comparing values.
        cfg.sources["agent.id"] = layer
    log = EventLog(repo, resolved, lock_timeout_s=cfg.lease.acquire_timeout_s, log_cfg=cfg.log)
    return log, cfg, fold(log.read_all(), strict=False)
