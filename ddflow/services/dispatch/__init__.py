"""The dispatcher: a pure planner over the scheduler core (DX-dispatch-plan).

`ddflow dispatch [--plan|--tick]` and MCP `ddflow_dispatch` answer the one question the
queue still left to a person or a master agent: *what do I start right now?* The planner
is pure (`plan` and the `steer` mapping): nothing starts, nothing writes, no clock and no
model -- the same inputs give the same plan, so a second call returns nothing new until
the state changes. The API half (`ddflow.api.dispatch`) reads the queue and the fleet and
renders the result; `--tick` records one `session.note` audit note so an advised plan is
auditable.
"""

from __future__ import annotations

from .plan import Move, Plan, Start, fill, plan, sort_ready, steer

__all__ = ["Move", "Plan", "Start", "fill", "plan", "sort_ready", "steer"]
