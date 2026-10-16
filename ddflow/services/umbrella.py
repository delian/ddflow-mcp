"""An umbrella's gates: rolled up from its sub-tasks, on the record (bug Bd37a921536).

An umbrella has no diff of its own: its work is its sub-tasks', each of which ran its own
pipeline. It still owes every gate an outcome -- silence is not a pass -- so completing a
settled umbrella records `skipped`, reason "rolled up from the sub-tasks", evidence
naming them, for each gate that has none. A gate that already holds an outcome keeps it.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog
from . import completion as CM
from . import gates as G

#: The blockers an umbrella sheds: it has no diff, so a rolled-up skip of implement/merge is
#: final, and no reviewer of its own exists for independence to be judged against.
SILENT = "gate(s) never run"
SHED = ("required gate(s) not passed", "reviewer independence not satisfied")


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


def blockers(st: State, cfg: Config, repo: Path, umbrella: str, found: list[str]) -> list[str]:
    """``found`` less the blockers it sheds, plus one naming any gate that
    holds a failed or partial outcome: a rolled-up skip is not a pass, but a recorded
    failure is not an absence of work to judge either."""
    kept = [b for b in found if not b.startswith(SHED)]
    it = st.items[umbrella]
    bad = [
        g
        for g in G.status(st, cfg, umbrella, G.load_gates(repo, cfg)).pipeline
        if it.gate_outcome(g) in ("failed", "partial")
    ]
    if bad:
        kept.append(f"gate(s) {', '.join(bad)} failed on the umbrella itself")
    return kept


def verdict(
    st: State, cfg: Config, item: str, *, repo: Path, model: str, log: EventLog, kids: list[str]
):
    """`completion.verdict` for a settled umbrella: its gates rolled up first, then judged
    with the blockers a no-diff item cannot meet shed (B651a63e574)."""
    held = blockers(st, cfg, repo, item, CM.verdict(st, cfg, item, repo=repo, model=model).blockers)
    # Roll up only a completion that will go through: a refused one records nothing.
    if not [b for b in held if not b.startswith(SILENT)]:
        roll_up(log, cfg, repo, st, item, kids)
        st = fold(log.read_all(), strict=False)
    v = CM.verdict(st, cfg, item, repo=repo, model=model)
    v.blockers = blockers(st, cfg, repo, item, v.blockers)
    return v
