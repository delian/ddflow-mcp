"""What every operation in this layer needs before it can answer anything.

Separate from `__init__` so a family module can import it without importing the
package that imports the family module. One file, one import edge, no cycle.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog, effective_agent_id


def _load(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
    """Config FIRST, then the log — because the identity depends on the config.

    This used to be `EventLog(repo, agent)` with an empty `agent`, which falls to
    `default_agent_id()` and reads neither `DDFLOW_AGENT` nor `[agent].id`. The argv
    path resolved all four layers; this one resolved one. Same connection, same call,
    two identities, two shards.
    """
    cfg = Config.load(repo)
    log = EventLog(repo, effective_agent_id(repo, cfg, agent))
    return log, cfg, fold(log.read_all(), strict=False)
