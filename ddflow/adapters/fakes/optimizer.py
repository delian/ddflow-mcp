"""`FakeOptimizer`: a scripted, recording `Optimizer`. No ddflow state, no clock.

By default it ranks by the candidates' terms then id and cuts in walk order (the same contract
as the default adapter). A test can pin the order (``ranking``) or force verdicts (``verdicts``)
and read every call back from ``calls``, so a caller is tested against the PORT, not against
the greedy algorithm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..optimizer import Candidate, Clash, CutResult, Hold, Placement, Slots, Verdict, fit
from . import fake


@fake
@dataclass
class FakeOptimizer:
    """``ranking``: ids best first (those not named follow, in id order). ``verdicts``: id to
    ``taken|held|cut`` forced by the test. ``calls``: ``(method, ctx)`` of every call."""

    name: str = "fake"
    ranking: list[str] = field(default_factory=list)
    verdicts: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)

    def order(self, ctx: Mapping[str, Any], candidates: Sequence[Candidate]) -> list[Placement]:
        self.calls.append(("order", dict(ctx)))
        pinned = {i: n for n, i in enumerate(self.ranking)}
        ranked = sorted(
            candidates,
            key=lambda c: (
                pinned.get(c.id, len(pinned)),
                () if c.id in pinned else tuple(v for _, v in c.terms),
                c.id,
            ),
        )
        return [Placement(c.id, n, c.terms) for n, c in enumerate(ranked)]

    def cut(
        self,
        ctx: Mapping[str, Any],
        ready: Sequence[Candidate],
        slots: Slots,
        *,
        hold: Hold | None = None,
        clash: Clash | None = None,
    ) -> CutResult:
        self.calls.append(("cut", dict(ctx)))
        taken: list[Candidate] = []
        out: list[Verdict] = []
        for c in ready:
            forced = self.verdicts.get(c.id, "")
            other = clash(c, taken) if clash is not None else ""
            if forced == "held" or (not forced and hold is not None and hold(c)):
                out.append(Verdict(c.id, "held"))
            elif other:
                out.append(Verdict(c.id, "clash", other))
            else:
                what = "cut" if forced == "cut" else fit(c, taken, slots)
                if what == "taken":
                    taken.append(c)
                out.append(Verdict(c.id, what))
        return CutResult(tuple(out))

    def explain(self, placement: Placement) -> str:
        self.calls.append(("explain", {}))
        return f"{placement.id}: rank {placement.rank} (fake)"
