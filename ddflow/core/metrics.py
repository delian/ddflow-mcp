"""The metrics `view` names `ddflow metrics` accepts.

Kept in `core` (not `services.metrics`) so the CLI surface can list them for argparse's
`choices` without reaching into `services` directly (surfaces-through-api) and without
importing `ddflow.api` eagerly -- `ddflow.api` reaches jinja2 and is only imported at call
time (`surfaces.tools._common._api`), so the tool table must not pull it in at import.
"""

from __future__ import annotations

VIEWS: tuple[str, ...] = (
    "summary",
    "forecast",
    "retro",
    "skips",
    "lanes",
    "stages",
    "locks",
    "tiny",
    "paths",
    "variants",
    "swarm",
    "slo",
    "batch",
)
