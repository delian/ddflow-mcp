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
    st = fold(log.read_all(), strict=False)
    # Leases this clone claimed under its pre-B190 bare id become its suffixed id's, HERE,
    # once -- so no holder comparison anywhere needs to know the old name (B205).
    from ..services.identity import rehome_pre_upgrade_leases

    if rehome_pre_upgrade_leases(log, cfg, st, layer):
        st = fold(log.read_all(), strict=False)
    # Workflow choices recorded in the log fill in wherever the config file is silent --
    # HERE, once, so every operation reads `cfg.flow.*` and sees the same answer.
    from ..services.choices import overlay

    overlay(cfg, st)
    _sample_flow(repo, log, cfg, st)
    return log, cfg, st


def _sample_flow(repo: Path, log: EventLog, cfg: Config, st: State) -> None:
    """Take an adaptive-parallelism sample when one is due (B-af-sampler).

    Here, because every operation loads the project through `_load`: `next`, `brief`,
    `claim` and `heartbeat` sample without a daemon, and heartbeats already run every
    `lease.heartbeat_s` on every platform. Throttled to one sample per
    `schedule.signal_interval_s`, writing only under the git-ignored `.ddflow/local/`,
    and never raising: a sample that cannot be taken costs this command nothing.
    """
    if cfg.schedule.parallel != "auto" or not (Path(repo) / ".ddflow").is_dir():
        return
    try:
        from ..infra import signals as SIG
        from ..services import flowstate as FL

        # `log.read_all`, not its result: the log is read only when a sample is due
        ctx = FL.FlowCtx(repo=Path(repo), cfg=cfg, state=st, events=log.read_all)
        FL.sample_if_due(ctx, SIG.HostSignals(repo))
    except Exception:
        return
