"""How big the launch brief of a subagent may be, and whether it goes lean (LS-launch-brief-budget).

The cap used to be one fixed number. It now follows the session start budget
(`services.startcost.effective_budget`): `agent_brief_budget_share` of it, between the floor and the
fixed ceiling, so a small window gets a small brief. Under `agent_brief_lean_chars` the brief drops the
standing sections the gates enforce anyway, and the lessons. Pure arithmetic on the session config:
the renderer (`services.agent_brief`) and the api call it.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import startcost as SC

#: Sections a LEAN brief leaves out, besides those the project switched off: their rules are
#: enforced by gates and hooks, and the brief says where to read them.
LEAN_OFF = ("tests", "reviews", "gates", "filing", "project", "lessons")

#: The line a lean brief ends with, so the omission is said, never silent.
LEAN_NOTE = (
    "_Lean brief (small context budget): the testing, review, gate and filing rules, the "
    "project's own text and the lessons are left out (`ddflow recall \"<topic>\"` finds lessons). `ddflow gate status <id>` names each gate's step; "
    "`ddflow_help` topic `manual` carries the rest._"
)


@dataclass(frozen=True)
class Sizing:
    cap: int  # characters; 0 = no ceiling
    lean: bool
    budget: int  # the effective start budget it came from, tokens (0 = none)


def sizing(session, target: str = "") -> Sizing:
    """The brief's cap and mode for a session of agent type ``target`` ("" = unknown)."""
    # Tokens to characters is an estimate (`start_bytes_per_token`, about four to a token), the same
    # one the start cost meter uses; a ceiling of 0 means none, and the floor applies to a DERIVED cap.
    budget = SC.effective_budget(session, target)
    ceiling = session.agent_brief_max_chars
    cap = ceiling
    if budget > 0:
        share = int(budget * session.agent_brief_budget_share * session.start_bytes_per_token)
        derived = max(session.agent_brief_min_chars, share)
        cap = min(ceiling, derived) if ceiling > 0 else derived
    lean = (
        bool(cap) and session.agent_brief_lean_chars > 0 and cap <= session.agent_brief_lean_chars
    )
    return Sizing(cap=cap, lean=lean, budget=budget)
