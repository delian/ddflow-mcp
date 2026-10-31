"""The Tier-0 seed pack a swarm lane receives at start (SW-seed-pack).

One deterministic, token-budgeted, redacted text: the item brief, the decisions that govern
its globs, the project's rules, ranked lessons and an optional handoff note. Everything
recorded is DATA: redacted (nothing machine-private or secret reaches another vendor's
lane), then wrapped in the shared ``ddflow-record`` fence (`core.provenance`) whose body
cannot close it. The handoff is written by an earlier lane, so it is always trust
``unknown``, whatever it claims.

``build`` is pure (arguments in, `Seed` out); ``from_state`` reads the folded state and the
rule files to fill a `SeedInput`. This is the one record-pack format: the handoff capsule
(LC-capsule) and the resume pack (CC-resume-pack) reuse ``fence_line``/``Knobs``, they do
not add a third. Priority when the budget is short: item, decisions, rules, lessons,
handoff; an entry that does not fit whole is cut, then dropped and named in ``Seed.dropped``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ...core.budget import chars_for
from ...core.provenance import DATA_RULE, Origin, decision_origin, fence, lesson_origin
from ..guidance.kinds import governing
from ..guidance.ruleview import project_rules
from ..redact_report import redactor

#: Section order is priority order.
ORDER = ("decisions", "rules", "lessons", "handoff")
_KIND = {"decisions": "decision", "rules": "rule", "lessons": "lesson", "handoff": "handoff"}
_MARK = "…"


@dataclass(frozen=True)
class Knobs:
    """Every number of the pack; ``from_config`` is the place a config section feeds them."""

    max_tokens: int = 1500
    max_decisions: int = 8
    max_rules: int = 7
    max_lessons: int = 6
    entry_chars: int = 600  # one decision/rule/lesson, before the fence
    item_chars: int = 1500  # the item body
    handoff_chars: int = 1200


DEFAULT = Knobs()


@dataclass(frozen=True)
class Rec:
    id: str
    title: str = ""
    body: str = ""
    trust: str = "agent"
    by: str = ""


@dataclass(frozen=True)
class SeedInput:
    item_id: str
    item_title: str = ""
    item_body: str = ""
    globs: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    decisions: Sequence[Rec] = ()
    rules: Sequence[Rec] = ()
    lessons: Sequence[Rec] = ()  # already ranked, best first
    handoff: str = ""
    handoff_by: str = ""


@dataclass(frozen=True)
class Seed:
    preamble: str
    sections: tuple[tuple[str, str], ...]
    dropped: tuple[str, ...] = ()


def default_redact() -> Callable[[str], str]:
    """The shared ``upstream`` profile: secrets, private addresses, hostnames, home paths."""
    r = redactor("upstream")
    return lambda s: r.text(s).text


def _cut(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: n - 1] + _MARK


def fence_line(
    kind: str, rec: Rec, redact: Callable[[str], str], cap: int, room: int
) -> str | None:
    """One fenced, single-line record of at most ``room`` chars (no newline), or None.

    Redacts first, then cuts (a key is never cut into a prefix the patterns miss), then
    shrinks the body until the escaped, fenced line fits."""
    head = _cut(redact(rec.title), 120)
    text = f"{head}: {redact(rec.body)}" if head and rec.body else head or redact(rec.body)
    origin = Origin(rec.trust, by=redact(rec.by))
    n = cap
    while True:
        line = fence(kind, redact(rec.id), _cut(text, n), origin)
        if len(line) <= room:
            return line
        if n == 0:
            return None
        n = max(0, n - max(1, len(line) - room))


def _head(name: str) -> int:
    """Chars a section header costs in `render`: newline, ``## name``, newline."""
    return len(name) + 5


def build(
    inp: SeedInput,
    knobs: Knobs = DEFAULT,
    *,
    redact: Callable[[str], str] | None = None,
) -> Seed:
    red = redact or default_redact()
    room = chars_for(knobs.max_tokens) - len(DATA_RULE)
    dropped: list[str] = []
    sections: list[tuple[str, str]] = []

    brief = Rec(inp.item_id, inp.item_title, inp.item_body, trust="unknown")
    meta = ""
    if inp.globs:
        meta += " writes: " + ", ".join(inp.globs) + "."
    if inp.needs:
        meta += " needs: " + ", ".join(inp.needs) + "."
    brief = Rec(brief.id, brief.title, f"{brief.body}{meta}", trust="unknown")
    line = fence_line("item", brief, red, knobs.item_chars, room - _head("item"))
    if line is None:
        raise ValueError(
            f"max_tokens={knobs.max_tokens} cannot hold the data notice and the item brief"
        )
    sections.append(("item", line))
    room -= _head("item") + len(line)

    pools = {
        "decisions": (inp.decisions[: knobs.max_decisions], knobs.entry_chars),
        "rules": (inp.rules[: knobs.max_rules], knobs.entry_chars),
        "lessons": (inp.lessons[: knobs.max_lessons], knobs.entry_chars),
    }
    if inp.handoff:
        pools["handoff"] = (
            (Rec("handoff", "", inp.handoff, trust="unknown", by=inp.handoff_by),),
            knobs.handoff_chars,
        )
    for name in ORDER:
        recs, cap = pools.get(name, ((), 0))
        lines: list[str] = []
        cost = _head(name)
        for r in recs:
            got = fence_line(
                _KIND[name], r, red, cap, room - cost - sum(map(len, lines)) - len(lines)
            )
            if got is None:
                dropped.append(r.id)
                continue
            lines.append(got)
        if lines:
            sections.append((name, "\n".join(lines)))
            room -= cost + sum(map(len, lines)) + len(lines) - 1
    return Seed(DATA_RULE, tuple(sections), tuple(dropped))


def render(seed: Seed) -> str:
    out = [seed.preamble]
    for name, text in seed.sections:
        out.append(f"## {name}\n{text}")
    return "\n".join(out)


def _rec(ident: str, title: str, body: str, origin: Origin) -> Rec:
    """A `Rec` with the author and trust the shared provenance rules give the record."""
    return Rec(ident, title, body, origin.trust, origin.by)


def from_state(
    state, repo: Path, item_id: str, *, handoff: str = "", handoff_by: str = ""
) -> SeedInput:
    """Fill a `SeedInput` from the folded ``state`` and the rule files of ``repo``.

    Decisions and lessons carry the trust `core.provenance` gives them; a rule file records
    no author trust, so it is shown as an agent's (never promoted to operator)."""
    item = state.items[item_id]
    scoped, always = governing(state, list(item.globs))
    text = set(
        re.findall(
            r"[a-z0-9]{4,}",
            f"{item.title} {getattr(item, 'body', '')} {' '.join(item.globs)}".lower(),
        )
    )

    def hits(les) -> tuple[int, str]:
        words = set(
            re.findall(r"[a-z0-9]{4,}", f"{les.title} {les.rule} {' '.join(les.tags)}".lower())
        )
        return (-len(text & words), les.id)

    lessons = sorted((les for les in state.lessons.values() if les.live), key=hits)
    return SeedInput(
        item_id=item.id,
        item_title=item.title,
        item_body=getattr(item, "body", "") or "",
        globs=tuple(item.globs),
        needs=tuple(item.needs),
        decisions=tuple(
            _rec(d.id, d.title, d.decision, decision_origin(d)) for d in [*scoped, *always]
        ),
        rules=tuple(
            Rec(r.id, r.title, r.body, "agent", str(r.provenance.get("by", "")))
            for r in project_rules(repo)
        ),
        lessons=tuple(_rec(les.id, les.title, les.rule, lesson_origin(les)) for les in lessons),
        handoff=handoff,
        handoff_by=handoff_by,
    )
