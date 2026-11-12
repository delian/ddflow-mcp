"""The gap-task plan (PAR-gap-tasks): one task per gap, deduped by (agent, row).

A gap is a catalog row a descriptor grades ``unverified``. This module COMPUTES the plan --
per agent the rows it has not answered yet, ordered so a gap's ``needs`` run descriptor-first
(descriptor, then probe, then hooks/mcp/skills, then driver, then parity) and deduped by
``(agent, row)`` so rerunning is harmless. It computes the plan only; filing is a flag's
prerogative, never automatic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import harnessreg as HR
from . import answers as AN
from . import catalog as CAT

#: The filing order a gap's ``needs`` follows: descriptor first, then probe, then the agent
#: surfaces, then the driver, then parity -- a fixed precedence, not a hand-built graph.
DESCRIPTOR_FIRST = ("descriptor", "probe", "hooks", "mcp", "skills", "driver", "parity")


@dataclass(frozen=True)
class Gap:
    agent: str
    feature: str
    axis: str


def rows(hs: list | None = None) -> list[Gap]:
    """The gaps: every ``(agent, feature)`` a descriptor answers ``unverified``, one entry
    each (deduped; a rerun adds none new)."""
    hs = list(HR.load() if hs is None else hs)
    catalog = [r for r in CAT.load().rows if r.axis in ("in_agent", "orchestrator")]
    out: list[Gap] = []
    seen: set[tuple[str, str]] = set()
    for h in hs:
        answer = AN.answers(h)
        for row in catalog:
            key = (h.id, row.feature)
            if answer[row.feature] == "unverified" and key not in seen:
                seen.add(key)
                out.append(Gap(h.id, row.feature, row.axis))
    return out


def by_agent(hs: list | None = None) -> dict[str, list[str]]:
    """``{agent: [feature, ...]}`` of that agent's gaps, in catalogue order."""
    hs = list(HR.load() if hs is None else hs)
    out: dict[str, list[str]] = {h.id: [] for h in hs}
    for gap in rows(hs):
        out[gap.agent].append(gap.feature)
    return out


def phase(agent: str) -> str:
    """The phase a gap task lives in, named for the agent: ``P-<agent>``."""
    return f"P-{agent}"
