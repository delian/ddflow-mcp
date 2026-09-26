"""Architectural decisions: record them, consult them, supersede them.

The CONSULT half is why this family is worth a typed layer. A decision nobody reads is
a filed document; `applicable()` is the mechanism that turns it into something an agent
about to write a set of paths is HANDED, without having to know it exists or guess a
search term. That only works if both surfaces can ask the same question and get the
same answer, which is what an `Outcome` per operation buys.

Every function here returns one. The human renderings live in
`surfaces/commands/decisions.py` and derive from `data` — they do not recompute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, csv_list
from ..core import outcome as O
from ..core.ids import auto_id
from ..infra.store import Store
from ._base import _load


def _plain(obj: Any) -> Any:
    """Dataclass -> dict, recursively. `surfaces.context._plain` does the same thing for
    the CLI; this layer cannot import a surface, and the wire shape is defined here."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _store(repo: Path, log, cfg: Config) -> Store:
    st = Store(repo, cfg)
    st.ensure(log)
    return st


@dataclass
class Draft:
    """The decision record, named once.

    These twelve fields were spelled out three times — argparse flags, the MCP input
    schema, and the event payload — and a field added to one of them is a field the
    other two silently drop. A dataclass with defaults also answers the lint that
    `decision_add(repo, title, decision, context, consequences, alternatives, globs,
    tags, sources, status, by, item, supersedes, ...)` was a 15-argument function: the
    argument count was a symptom of the missing type, not a reason to shorten the name.

    `globs`, `tags`, `sources` and `supersedes` arrive as comma-separated strings
    because that is what both an argparse flag and a JSON string field give you;
    `csv_list` is the ONE parser for that notation.
    """

    title: str
    decision: str = ""
    id: str = ""
    context: str = ""
    consequences: str = ""
    alternatives: str = ""
    globs: str = ""
    tags: str = ""
    sources: str = ""
    status: str = "accepted"
    by: str = ""
    item: str = ""
    supersedes: str = ""


def decision_add(repo: Path, draft: Draft, *, agent: str = "") -> O.Outcome:
    """Record a decision. Refuses without a stated DECISION, not merely a discussion.

    The refusal is the point: a record saying what was *discussed* is indistinguishable
    from notes, and the next agent cannot act on it.
    """
    if not draft.decision:
        return O.failed(
            "decision.recorded",
            "--decision is required: the record must say what was DECIDED, not only "
            "what was discussed.",
        )
    log, _cfg, _st = _load(repo, agent)
    did = draft.id or auto_id("D", draft.title, draft.decision)
    fields: dict[str, Any] = {
        "title": draft.title,
        "context": draft.context,
        "decision": draft.decision,
        "consequences": draft.consequences,
        "alternatives": draft.alternatives,
        "globs": csv_list(draft.globs),
        "tags": csv_list(draft.tags),
        "sources": csv_list(draft.sources),
        "status": draft.status,
        "decided_by": draft.by,
        "item": draft.item,
        "supersedes": csv_list(draft.supersedes),
    }
    log.append("decision.recorded", did, fields)
    return O.ok(
        "decision.recorded",
        id=did,
        status=draft.status,
        supersedes=fields["supersedes"],
        # Carried so BOTH surfaces can warn. A decision with no globs cannot be
        # surfaced automatically to an agent working the code it governs -- it will
        # only ever be found by someone who already went looking. That warning used to
        # print in human mode only.
        ungoverned=not fields["globs"],
    )


def decision_supersede(
    repo: Path, item: str, *, by: str = "", reason: str = "", agent: str = ""
) -> O.Outcome:
    """Replace a decision. Never deletes one: how the architecture got here is the part
    a rebuild most needs."""
    log, _cfg, st = _load(repo, agent)
    if item not in st.decisions:
        return O.failed("decision.superseded", f"no such decision {item!r}", id=item)
    if not by:
        return O.failed(
            "decision.superseded",
            "--by <new decision id> is required: a decision is never simply deleted, "
            "it is replaced by one that says what is true now.",
            id=item,
        )
    log.append("decision.superseded", item, {"by": by, "reason": reason})
    return O.ok("decision.superseded", id=item, by=by, reason=reason)


def decision_show(repo: Path, item: str) -> O.Outcome:
    """One decision in full. The wire body is the decision object itself."""
    _log, _cfg, st = _load(repo)
    d = st.decisions.get(item)
    if not d:
        return O.failed("decision.show", f"no such decision {item!r}", id=item, decision=None)
    return O.ok("decision.show", id=item, decision=_plain(d))


def decision_applicable(repo: Path, item: str) -> O.Outcome:
    """Decisions governing an item's declared files.

    Exit 2 when nothing governs it: "no decision applies" is an answer, and reporting
    it as success is how a caller comes to believe it consulted something.
    """
    from ..core.schedule import conflicts

    _log, _cfg, st = _load(repo)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(
            "decision.applicable",
            f"no such item {item!r}{gone}",
            id=item,
            applicable=[],
            project_wide=[],
        )
    hits = [d for d in st.decisions.values() if d.live and d.globs and conflicts(it.globs, d.globs)]
    wide = [d for d in st.decisions.values() if d.live and not d.globs]
    data: dict[str, Any] = {
        "id": item,
        "globs": list(it.globs),
        "applicable": [_plain(d) for d in hits],
        "project_wide": [_plain(d) for d in wide],
    }
    if not hits and not wide:
        return O.nothing(
            "decision.applicable",
            f"No architectural decisions govern {item}'s files "
            f"({', '.join(it.globs) or 'no globs declared'}).",
            **data,
        )
    return O.ok("decision.applicable", **data)


def decision_search(repo: Path, query: str, *, limit: int = 20) -> O.Outcome:
    """Free-text search across decisions."""
    log, cfg, _st = _load(repo)
    hits = _store(repo, log, cfg).search("decisions", query, limit)
    data: dict[str, Any] = {"query": query, "hits": hits, "count": len(hits)}
    if not hits:
        return O.nothing("decision.search", "no matching decisions", **data)
    return O.ok("decision.search", **data)


def decision_list(repo: Path, *, all: bool = False) -> O.Outcome:
    """Decisions in force; `all` includes the superseded ones."""
    _log, _cfg, st = _load(repo)
    live = [d for d in st.decisions.values() if d.live]
    dead = [d for d in st.decisions.values() if not d.live]
    rows = live + dead if all else live
    data: dict[str, Any] = {
        "rows": [_plain(d) for d in sorted(rows, key=lambda x: x.at)],
        "live": len(live),
        # How many exist but were not shown -- so the human renderer can say so without
        # folding a second time, and a machine caller can tell "none recorded" from
        # "none in force".
        "hidden": 0 if all else len(dead),
        "all": all,
    }
    if not rows:
        return O.nothing(
            "decision.list",
            "No architectural decisions recorded.\n"
            "  ddflow decision add --title '...' --decision '...' --globs 'src/x/*'",
            **data,
        )
    return O.ok("decision.list", **data)
