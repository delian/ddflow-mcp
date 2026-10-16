"""An umbrella's gates: rolled up from its sub-tasks, on the record (bug Bd37a921536).

An umbrella has no diff of its own: its work is its sub-tasks', each of which ran its own
pipeline. It still owes every gate an outcome -- silence is not a pass -- so completing a
settled umbrella records `skipped`, reason "rolled up from the sub-tasks", evidence
naming them, for each gate that has none. A gate that already holds an outcome keeps it.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.model import State
from ..infra.log import EventLog
from . import gates as G
from .completion import OPEN_BUG_BLOCKER

#: The blockers an umbrella keeps: its open bug, a gate no one recorded, a person's gate.
KEPT = (OPEN_BUG_BLOCKER, "gate(s) never run", "awaiting a person")


def roll_up(
    log: EventLog, cfg: Config, repo: Path, st: State, umbrella: str, children: list[str]
) -> list[str]:
    """Record the rolled-up outcome for each silent gate of ``umbrella``; the gates done.

    A gate that cannot be recorded (a human checkpoint, skipping disabled) stays silent,
    so the completion verdict still names it."""
    gdefs = G.load_gates(repo, cfg)
    silent = G.status(st, cfg, umbrella, gdefs).silent
    done: list[str] = []
    for gate in silent:
        try:
            G.record(
                log,
                cfg,
                umbrella,
                gate,
                "skipped",
                reason=f"rolled up from the sub-tasks: {', '.join(children)}",
                evidence={"rolled_up_from": list(children)},
                gates=gdefs,
            )
        except ValueError:
            continue
        done.append(gate)
    return done
