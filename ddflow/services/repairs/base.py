"""The shape of a versioned data repair (decision D-upgrade-model (4), B-upgrade.5-repairs).

A repair is `{id, since, detect, repair}` plus what an operator reads: a title, the one-line
action applying it takes, the bugs whose damage it mends and who may apply it. `detect` is
pure over a `Context` (the log, its fold, the config and the working tree) and returns
findings, each with a stable key; `repair` turns findings into CORRECTIVE events to append
(and, for a generated file, rewrites that file the way `ddflow export --update` does). No
repair edits or deletes an existing log line: history is never rewritten.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.events import Event
from ...core.model import State, fold
from ...infra.log import EventLog

#: An event a repair asks to append: `(kind, subject, data)`.
Corrective = tuple[str, str, dict[str, Any]]

#: Who may apply a repair. `agent`: any agent running the repairs, unasked (the result is
#: corrective events only an older log lacks). `operator`: only when named explicitly --
#: the repair records a human judgement (e.g. "this unknown author is trusted").
AGENT = "agent"
OPERATOR = "operator"


class Unavailable(Exception):
    """A detector could not run (no git, an unreadable file). Reported as `unavailable`,
    never as "no findings": a check that did not run must not read as clean."""


@dataclass(frozen=True)
class Finding:
    #: Stable across runs for the same damage: what `repair.applied` records so the finding
    #: is never offered again.
    key: str
    #: One line for a human: what is damaged and where.
    detail: str


@dataclass
class Context:
    repo: Path
    log: EventLog
    cfg: Config
    events: list[Event]
    st: State


def context(repo: Path, log: EventLog, cfg: Config) -> Context:
    """A fresh read of the log: a repair applied earlier in the same run is visible."""
    events = log.read_all()
    return Context(Path(repo), log, cfg, events, fold(events, strict=False))


@dataclass(frozen=True)
class Repair:
    id: str
    #: The first ddflow release that no longer causes this damage: logs written before it
    #: may carry it, logs written after should not.
    since: str
    title: str
    #: What applying it does, in one line.
    action: str
    detect: Callable[[Context], list[Finding]]
    repair: Callable[[Context, list[Finding]], list[Corrective]]
    #: The bugs whose damage this repair mends (the data-damage registry rule).
    bugs: tuple[str, ...] = ()
    consent: str = AGENT

    def data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "since": self.since,
            "title": self.title,
            "action": self.action,
            "bugs": list(self.bugs),
            "consent": self.consent,
        }
