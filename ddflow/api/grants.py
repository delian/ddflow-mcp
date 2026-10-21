"""`grant add` / `grant list` (DX-operator-grants-recorded): the operator's recorded
permission for a round the review budget refuses. CLI only, like `ddflow approve`."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import grants as GR
from ._base import _load


def grant_add(
    repo: Path, *, item: str, gate: str, kind: str, count: int, reason: str,
    when: str = "", agent: str = "",
) -> O.Outcome:  # fmt: skip
    """A PERSON records a grant. Refused under an agent identity."""
    log, cfg, _st = _load(repo, agent)
    try:
        g = GR.add(
            log, cfg, item=item, gate=gate, kind=kind, count=count, reason=reason,
            when=when, requested_agent=agent,
        )  # fmt: skip
    except GR.GrantRefused as exc:
        return O.refused("grant.add", str(exc))
    return O.ok(
        "grant.add", grant=g.row(), text=f"recorded {GR.describe(g)} -- {item}.{gate}, {g.by}"
    )


def grant_list(repo: Path, *, item: str = "", agent: str = "") -> O.Outcome:
    """Every grant (or the item's): who, when, how many used."""
    _log, _cfg, st = _load(repo, agent)
    rows = [g for g in GR.grants(st) if not item or g.item == item]
    text = "\n".join(
        f"{GR.describe(g)} -- {g.item}.{g.gate}, {g.by} at {g.at}: {g.reason}" for g in rows
    )
    return O.ok("grant.list", grants=[g.row() for g in rows], text=text or "no grants")
