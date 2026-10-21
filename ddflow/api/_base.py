"""What every operation in this layer needs before it can answer anything.

Separate from `__init__` so a family module can import it without importing the
package that imports the family module. One file, one import edge, no cycle.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog
from ..services import identity as ID
from ..services import shardmarks as SM
from ..services.choices import overlay
from ..services.leaseclock import localize

#: The loads made inside one `load_scope`, by (project, agent); None outside any scope.
_SCOPE: ContextVar[dict[tuple[str, str], tuple[EventLog, Config, State]] | None] = ContextVar(
    "ddflow_load_scope", default=None
)


@contextmanager
def load_scope() -> Iterator[None]:
    """Within it, `_load` of one project and agent folds the log ONCE and hands every later
    caller the same (log, config, state). For a READ-ONLY burst such as the MCP handshake,
    which asks several readers about the same moment (B-mcp-initialize-folds-log): each
    loading on its own folded the whole log again, ~0.5 s apiece on a real corpus."""
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _load(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
    """`_load_fresh`, or this scope's earlier answer for the same project and agent."""
    scope = _SCOPE.get()
    if scope is None:
        return _load_fresh(repo, agent)
    key = (str(Path(repo).resolve()), agent)
    if key not in scope:
        scope[key] = _load_fresh(repo, agent)
    return scope[key]


def _load_fresh(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
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
    # env var actually set (`identity.bind`: the layer that actually WON).
    log, who = ID.open_log(repo, cfg, agent, bind_cfg=True)
    st = fold(log.read_all(), strict=False)
    # The high-water mark of every shard rises with what this clone sees (Ba24a8d32ee): a later
    # shard with FEWER events is a loss `doctor` and `brief` name. Cheap, never raises.
    SM.observe(repo, log)
    # Leases this clone claimed under its pre-B190 bare id become its suffixed id's, HERE,
    # once -- so no holder comparison anywhere needs to know the old name (B205).
    if ID.rehome_pre_upgrade_leases(log, cfg, st, who.source):
        st = fold(log.read_all(), strict=False)
    # Workflow choices recorded in the log fill in wherever the config file is silent --
    # HERE, once, so every operation reads `cfg.flow.*` and sees the same answer.

    # A lease of ANOTHER clone is timed from when this clone first saw it renewed, not by the
    # claimant's wall clock (B5a6651cd8e). Here, once, like the overlay: every command's `st`.
    localize(st, log, time.time())
    overlay(cfg, st)
    _run_ticks(repo, log, cfg, st)
    return log, cfg, st


def _run_ticks(repo: Path, log: EventLog, cfg: Config, st: State) -> None:
    """Run the periodic work that is due (`services.ticks`): the adaptive-parallelism sample
    (B-af-sampler) and whatever else registered. Here, the ONE opportunistic call site,
    because every operation loads the project through `_load`: `next`, `brief`, `claim`
    and `heartbeat` tick without a daemon, and heartbeats already run every
    `lease.heartbeat_s` on every platform. Each tick is budgeted and throttled by the
    registry, writes only under the git-ignored `.ddflow/local/`, and never raises: a tick
    that cannot run costs this command nothing.
    """
    if not (Path(repo) / ".ddflow").is_dir():
        return
    try:
        from ..services import ticks as TK

        # `log.read_all`, not its result: the log is read only when a tick asks for it
        TK.run_due(TK.TickCtx(Path(repo), cfg, st, log.read_all))
    except Exception:
        return


def folded(log: EventLog) -> State:
    """The state of ``log`` as read now, with another clone's leases timed by when this clone
    saw them renewed (B5a6651cd8e) -- for a surface, which may not import the service."""
    return localize(fold(log.read_all(), strict=False), log, time.time())
