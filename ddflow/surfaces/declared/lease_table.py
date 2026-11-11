"""The `lease` command, declared once: who holds a lease, the table half of `lanes --coordinator`.

CLI only (the `lanes` tool serves it with `coordinator`); registered by convention
(`surfaces/parsers/by_convention.py`).
"""

from __future__ import annotations

from ..registry import Command
from ..tools._common import _api


def _text(out, a) -> str:
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("lease",),
        summary="the lease holders and how long each has been quiet (idle ones flagged); the holders of `lanes --coordinator`",
        reason="the lanes tool serves it: ddflow_lanes with coordinator",
        via=("ddflow_lanes", "coordinator"),
        call=lambda repo, a, agent: _api().lanes(repo, view="lease", agent=agent),
        payload=("holders", "text"),
        render=_text,
    ),
)
