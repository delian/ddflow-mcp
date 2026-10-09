"""The context pack: ranked candidates cut to one budget, each cited and fenced as data.

Recall once enforced its budget twice with different meanings (the MCP bound counted the
JSON it returned and took one hit per source in turn; the CLI renderer counted printed
characters in table order and stopped at the first overflow) while the API enforced
nothing. Every caller that hands an agent a budgeted reading now builds a `Pack` here,
once, in the API layer (D-unify, B-uni-context-pack.2-pack):

1. candidates arrive in rank order, per source (the search core's order);
2. a candidate whose text repeats one already taken is folded away (`textsim.digest`:
   identical up to case and whitespace -- the same identity the add-time check uses);
3. the rest are taken one per source in turn, so no source is crowded out, until the
   `Budget` is spent; a hit that does not fit is skipped, a smaller one behind it may still
   fit, and the very first hit is kept whole whatever it costs, so a tiny budget still
   answers and a fence is never cut open;
4. every candidate carries its citation (`source:id`) and its provenance fence
   (`core/provenance.py`), built when the candidate is made.

The cost of a hit is the size, in the budget's unit, of what the reader is shown: its
headline and its (fenced) body. A source's heading and the data rule that opens a recall
are not counted; they are the same for every answer.

Pure: no I/O. The caller makes the candidates (`candidate`) from its search results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core import provenance as PV
from ..core import textsim
from ..core.budget import Budget
from ..infra.store import summarise_row


@dataclass(frozen=True)
class Candidate:
    """One search hit on its way into a pack."""

    source: str
    id: str
    #: The record as the index holds it (what the CLI renders from).
    row: dict
    #: The hit as the `--json` / MCP body carries it: id, kind, headline, fenced body, raw.
    hit: dict
    #: The unfenced headline and body: what the duplicate check reads.
    head: str = ""
    body: str = ""

    @property
    def cite(self) -> str:
        """Where the hit came from, as one token: ``decisions:D-unify``."""
        return f"{self.source}:{self.id}"

    @property
    def shown(self) -> str:
        """What the reader is shown of the hit, for its cost."""
        return f"{self.hit.get('headline', '')}\n{self.hit.get('body', '')}"


def candidate(table: str, label: str, row: dict) -> Candidate:
    """A search hit made into a candidate: summarised, cited and, when it is somebody's
    words, fenced as data with its author and trust.

    JSON quoting is not a fence -- an agent reads the string, not the quotes -- so the
    body travels inside the fence and the headline outside it is only the id and the
    provenance sentence.
    """
    head, body = summarise_row(table, row)
    hit: dict[str, Any] = {"id": row.get("id"), "kind": label, "headline": head, "body": body}
    origin = PV.hit_origin(table, row)
    if origin is not None:
        kind = PV.hit_kind(table, row)
        # A prompt's headline is ddflow's own label (date, `operator asked:`, `SESSION
        # SUMMARY (sid):`), never the record's text, so it stays outside the fence.
        tag = f" {head}" if table == "prompts" and head else ""
        hit["headline"] = f"{row.get('id')}{tag} ({origin.label()})"
        hit["body"] = PV.fence(
            kind, str(row.get("id")), head + (f": {body}" if body else ""), origin
        )
        if row.get("provenance"):
            hit["provenance"] = row["provenance"]
    hit["raw"] = row
    return Candidate(table, str(row.get("id")), row, hit, head, body)


@dataclass(frozen=True)
class Pack:
    """What fit: the kept candidates per source, in rank order, and what was left out."""

    kept: dict[str, list[Candidate]]
    budget: Budget
    #: Candidates offered, duplicates included.
    total: int = 0
    #: Folded into an earlier hit with the same text.
    duplicates: int = 0
    cited: tuple[str, ...] = field(default=())

    @property
    def shown(self) -> int:
        return sum(len(v) for v in self.kept.values())

    @property
    def truncated(self) -> bool:
        """True when the budget (not a duplicate) left a hit out."""
        return self.shown + self.duplicates < self.total

    def note(self) -> str:
        """The statement of what was left out; '' when nothing was."""
        parts = []
        if self.truncated:
            parts.append(
                f"truncated: showing {self.shown} of {self.total - self.duplicates} hits "
                f"within max_chars={self.budget.limit}"
            )
        if self.duplicates:
            parts.append(f"{self.duplicates} repeated hit(s) folded into the first")
        return "; ".join(parts)


def pack(
    candidates: Mapping[str, Sequence[Candidate]], budget: Budget, *, dedupe: bool = True
) -> Pack:
    """Cut ``candidates`` (source -> hits in rank order) to ``budget``."""
    kept: dict[str, list[Candidate]] = {src: [] for src in candidates}
    seen: set[str] = set()
    used = total = duplicates = 0
    depth = max((len(v) for v in candidates.values()), default=0)
    for rank in range(depth):
        for src, hits in candidates.items():
            if rank >= len(hits):
                continue
            total += 1
            cand = hits[rank]
            key = (
                textsim.digest(cand.head, cand.body) if dedupe and (cand.head or cand.body) else ""
            )
            if key and key in seen:
                duplicates += 1
                continue
            cost = budget.cost(cand.shown)
            # The first hit is kept whatever it costs: a tiny budget still answers.
            if used and used + cost > budget.limit:
                continue
            kept[src].append(cand)
            used += cost
            if key:
                seen.add(key)
    return Pack(
        kept={s: v for s, v in kept.items() if v},
        budget=budget,
        total=total,
        duplicates=duplicates,
        cited=tuple(c.cite for v in kept.values() for c in v),
    )
