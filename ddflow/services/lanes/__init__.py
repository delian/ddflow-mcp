"""The lane ledger and advisor: a read-only projection of the event log.

A LANE is an agent identity that works this project. Everything here is a pure function of
the log (events, the state folded from them, the config and `now`): nothing is spawned or
killed, no daemon runs, and a stop whose reason the log does not record is reported as
unknown, never guessed. Modules: `states` (the vocabulary and the heartbeat rule),
`ledger` (events to lanes), `advise` (moves), `brief` (a lane's first prompt) and
`render` (plain text).
"""

from __future__ import annotations

from .advise import Move, advise
from .brief import lane_brief
from .ledger import Lane, build, find
from .redraw import watch
from .render import render_advice, render_ledger
from .safe import safe

__all__ = [
    "Lane",
    "Move",
    "advise",
    "build",
    "find",
    "lane_brief",
    "render_advice",
    "render_ledger",
    "safe",
    "watch",
]
