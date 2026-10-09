"""One injection path for guidance (D-unify, B-uni-guidance-inject).

Rules and decisions reach an agent through four doors: the brief, the claim, the
review-gate prompts and the other gate prompts. Each was going to grow its own selection,
its own budget and its own idea of what to cite. This is the one path they share:

1. **Select.** The live guidance among the project's rules (the definitions in the log) and
   decisions that governs the work (`resolve`): the item's files, the gate, the item's tags.
2. **Rank.** Pinned first, then what applies to all work (scope ``always``), then by how hard
   it binds (block, warn, advisory), how specific its scope is (a gate, then files, then a
   category) and its priority; ties by id.
3. **Pack.** PINNED guidance -- explicitly pinned, or an always-scope rule that BLOCKS -- is
   never trimmed, whatever the budget. (Merely naming no files is not a pin: a project with a
   dozen such decisions would get a page of them at every claim.) The rest goes through the
   context pack
   (`services.contextpack.pack`): duplicates folded by text, cut to the budget's remainder
   in rank order, every kept hit cited by ``kind:id``.
4. **Fence.** Every body is somebody's words and travels inside the provenance fence
   (`core.provenance`), with who recorded it and whether to trust it, after the one line that
   says fenced text is data.

A review gate's block adds the instruction to VERIFY the change against each item and to cite
its id in a finding; every other door cites it for the reader to look up. Pure but for
reading the log's state and the files it is handed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...core import provenance as PV
from ...core import textsim
from ...core.budget import Budget
from ...core.model import State
from ...core.schedule import shared_globs
from ...core.textcut import clip
from ..contextpack import Candidate, pack
from . import deffields as DF
from .kinds import RULE, decision_record
from .record import GuidanceRecord
from .resolve import ALWAYS, CATEGORY, GATE, GLOBS, Applies, resolve

#: The order guidance binds in: a failing check, a warning, advice.
ENFORCEMENT_RANK = {"block": 0, "warn": 1, "advisory": 2}
#: The most specific scope first: guidance written for THIS gate or THESE files before the
#: project-wide kind.
SPECIFICITY = {GATE: 0, GLOBS: 1, CATEGORY: 2, ALWAYS: 3}
#: How much of one unpinned body is quoted; the whole of it is a lookup away.
BODY_CHARS = 600
#: What the share of the brief's budget a gate or review door may use is (the decisions' own).
DOOR_SHARE = 0.25
#: How many cut-out ids the closing line names; the rest are counted.
NAMED = 6

VERIFY = (
    "Verify the change complies with each item below. For one it violates, report a finding "
    "that cites its id in brackets, e.g. [D-example]; a `block` item is a defect, a `warn` "
    "item a risk, an `advisory` one a suggestion."
)
CITE = "Cite an item's id when you rely on it."


def always(rec: GuidanceRecord) -> bool:
    """Guidance that applies to all work (no files, category or gate): it ranks ahead of
    guidance scoped to something, but is not for that reason immune to the budget."""
    return rec.scope.always


def pinned(rec: GuidanceRecord) -> bool:
    """Guidance handed over whole, whatever the budget: one that is explicitly PINNED
    (``ext["pinned"]``, B-dec-pinned) or an always-scope rule that BLOCKS. A project with a
    dozen decisions that merely name no files must not get a page of them at every claim
    (Bf7879835fd): those are ranked first and spend the budget like the rest."""
    return bool(rec.ext.get("pinned")) or (always(rec) and rec.enforcement == "block")


def rank_key(a: Applies) -> tuple:
    """Sort key, best first: pinned, applies to all work, enforcement, scope specificity,
    priority, then id."""
    rec = a.record
    return (
        not pinned(rec),
        not always(rec),
        ENFORCEMENT_RANK.get(rec.enforcement, len(ENFORCEMENT_RANK)),
        SPECIFICITY.get(a.reason, len(SPECIFICITY)),
        -rec.priority,
        rec.id,
    )


def origin_of(rec: GuidanceRecord) -> PV.Origin:
    """Who wrote ``rec``, for its fence: as `provenance.decision_origin` reads a decision."""
    by = str(rec.provenance.get("by", ""))
    decided_by = str(rec.ext.get("decided_by", ""))
    if "imported" in rec.tags:
        return PV.Origin(PV.IMPORTED, by, ", ".join(rec.sources))
    if decided_by.strip().lower() == PV.OPERATOR:
        return PV.Origin(PV.OPERATOR, by)
    return PV.Origin(PV.AGENT, by or (decided_by if decided_by != PV.AGENT else ""))


def collect(state: State) -> list[GuidanceRecord]:
    """Every record the log holds that could govern work: the live rules (definitions) and
    every decision, in id order within a kind."""
    rules = []
    for d in sorted(state.defs.values(), key=lambda d: d.id):
        if d.kind == RULE.kind and d.live:
            rec = DF.from_fields(d.id, d.fields, d.provenance, RULE)
            rec.provenance = {**rec.provenance, "by": d.by}
            rules.append(rec)
    decisions = [decision_record(d) for d in sorted(state.decisions.values(), key=lambda d: d.id)]
    return rules + decisions


@dataclass(frozen=True)
class Injection:
    """What the path hands over: everything that governs, what the text carries, what the
    budget left out."""

    #: Every item that governs, ranked.
    applied: tuple[Applies, ...] = ()
    #: The items the text carries, in order.
    shown: tuple[Applies, ...] = ()
    #: Ids the budget left out (never a pinned one) and the count of repeats folded away.
    trimmed: tuple[str, ...] = ()
    duplicates: int = 0
    text: str = ""

    @property
    def cited(self) -> tuple[str, ...]:
        return tuple(f"{a.record.kind}:{a.record.id}" for a in self.shown)

    @property
    def pinned(self) -> tuple[str, ...]:
        return tuple(a.record.id for a in self.shown if pinned(a.record))

    def records(self, kind: str) -> list[GuidanceRecord]:
        """The shown records of one kind, in rank order."""
        return [a.record for a in self.shown if a.record.kind == kind]


def _line(a: Applies, *, cap: int | None) -> str:
    rec = a.record
    body = f"{rec.title}: {rec.body}".strip(": ") if rec.title else rec.body
    if cap is not None:
        body = clip(body, cap, marker=" …", boundary="word")
    tags = [rec.kind, rec.enforcement] + (["pinned"] if pinned(rec) else [])
    fenced = PV.fence(rec.kind, rec.id, body, origin_of(rec))
    return f"- [{rec.id}] ({', '.join(tags)}) {fenced}"


def _candidate(a: Applies) -> Candidate:
    rec = a.record
    line = _line(a, cap=BODY_CHARS)
    return Candidate(
        source=rec.kind,
        id=rec.id,
        row={},
        hit={"id": rec.id, "kind": rec.kind, "headline": "", "body": line},
        head=rec.title,
        body=rec.body,
    )


def inject(
    records: Iterable[GuidanceRecord],
    *,
    globs: Sequence[str] = (),
    gate: str = "",
    categories: Iterable[str] = (),
    shared: list[str] | None = None,
    budget: Budget | None = None,
    verify: bool = False,
    heading: str = "## Guidance that governs this work",
) -> Injection:
    """Select, rank, pack and fence the guidance that governs work on ``globs`` at ``gate``.

    ``budget`` bounds the UNPINNED guidance (None: no bound); pinned guidance is whole and
    comes off the budget first. ``verify`` is for a review gate: its block tells the reviewer
    to check the change against each item and cite the id.
    """
    applied = sorted(
        resolve(records, globs=globs, gate=gate, categories=categories, shared=shared),
        key=rank_key,
    )
    if not applied:
        return Injection()
    head = [a for a in applied if pinned(a.record)]
    lines = [_line(a, cap=None) for a in head]
    kept, trimmed, duplicates = _fit([a for a in applied if not pinned(a.record)], lines, budget)
    lines += [_line(a, cap=BODY_CHARS) for a in kept]
    if trimmed:
        lines.append(_cut_line(trimmed))
    intro = [heading, "", f"_{PV.DATA_RULE}_", "", f"_{VERIFY if verify else CITE}_", ""]
    return Injection(
        applied=tuple(applied),
        shown=tuple(head + kept),
        trimmed=tuple(trimmed),
        duplicates=duplicates,
        text="\n".join(intro + lines) + "\n",
    )


def _fit(
    rest: list[Applies], pinned_lines: list[str], budget: Budget | None
) -> tuple[list[Applies], list[str], int]:
    """The unpinned items that fit what the pinned lines leave of ``budget`` (all, with no
    budget): (kept in rank order, ids cut, repeats folded away)."""
    if budget is None or not rest:
        return list(rest), [], 0
    left = budget.limit - sum(budget.cost(ln) for ln in pinned_lines)
    if left <= 0:
        return [], [a.record.id for a in rest], 0
    got = pack({"guidance": [_candidate(a) for a in rest]}, Budget(left, budget.unit))
    taken = {c.id for c in got.kept.get("guidance", [])}
    kept = [a for a in rest if a.record.id in taken]
    # `pack` folds a repeat by not keeping it; a repeat is not "cut to the budget".
    seen = {_text_key(a) for a in kept}
    cut = [a.record.id for a in rest if a.record.id not in taken and _text_key(a) not in seen]
    return kept, cut, got.duplicates


def _text_key(a: Applies) -> str:
    """The identity `pack` folds repeats by."""
    return textsim.digest(a.record.title, a.record.body)


def _cut_line(trimmed: list[str]) -> str:
    """The closing line that names what the budget left out."""
    more = f" and {len(trimmed) - NAMED} more" if len(trimmed) > NAMED else ""
    return (
        f"- _{len(trimmed)} more cut to the budget: {', '.join(trimmed[:NAMED])}{more}; "
        "`ddflow decision applicable <item>` / `ddflow rule list` have the rest._"
    )


def for_work(
    cfg,
    state: State,
    globs: Sequence[str],
    tags: Iterable[str] = (),
    *,
    gate: str = "",
    budget: Budget | None = None,
    verify: bool = False,
    heading: str = "## Guidance that governs this work",
) -> Injection:
    """`inject` over everything the log holds, for work on ``globs`` (tagged ``tags``)."""
    return inject(
        collect(state),
        globs=list(globs),
        gate=gate,
        categories=list(tags),
        shared=shared_globs(cfg),
        budget=budget,
        verify=verify,
        heading=heading,
    )


def for_item(cfg, state: State, item: str, **kwargs) -> Injection:
    """`for_work` for one queue item: its files and tags (nothing for an unknown item)."""
    it = state.items.get(item)
    if it is None or it.removed:
        return Injection()
    return for_work(cfg, state, it.globs, it.tags, **kwargs)


def door_budget(cfg) -> Budget:
    """What a gate or review door may spend on unpinned guidance: the decisions' share of
    the brief's budget."""
    return Budget(int(cfg.session.brief_max_tokens * DOOR_SHARE), "tokens")
