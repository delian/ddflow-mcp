"""`ddflow verify <id>`: the claims behind a completion, re-derived (B-verify-check)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import verify as V
from ._base import _load


def verify(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed("verify", f"no such item {item!r}", id=item)
    rep = V.check(repo, cfg, st, log.read_all(), item)
    data = rep.as_data()
    if not rep.completed:
        return O.nothing(
            "verify", f"{item} is {it.state}, not done: there is no completion to verify", **data
        )
    if rep.failed:
        bad = "; ".join(f"{c.id}: {c.detail}" for c in rep.claims if c.status == V.FAIL)
        return O.Outcome(kind="verify", data=data, exit=O.FAIL, reason=f"{item}: {bad}")
    return O.ok("verify", **data)
