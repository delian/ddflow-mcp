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

import ddflow.api._dedupe as DD

from ..config import Config, csv_list
from ..core import ids as IDS
from ..core import outcome as O
from ..core.plain import plain as _plain
from ..infra.store import Store
from ..services.export.query import ExportError, _cutoff
from ..services.guidance.kinds import governing
from ._base import _load


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
    #: What the adder says about a possible duplicate (``_dedupe.Answer``).
    answer: DD.Answer | None = None


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
    log, cfg, st = _load(repo, agent)
    minted = IDS.mint(
        cfg,
        st,
        "decision",
        events=log.read_all,
        given=draft.id,
        hash_parts=(draft.title, draft.decision),
    )
    did = minted.id
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="decision",
            event_kind="decision.recorded",
            rid=did,
            title=draft.title,
            body="\n".join(x for x in (draft.context, draft.decision) if x),
            item=draft.item,
        ),
        draft.answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "decision.recorded")
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
    with log.transaction():
        minted = IDS.confirm(
            cfg,
            "decision",
            minted,
            used=IDS.used_now(log),
            hash_parts=(draft.title, draft.decision),
        )
        log.append("decision.recorded", did, fields | IDS.key_field(minted) | chk.fields)
        DD.after_add(log, cfg, did, chk)
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
        **chk.data(),
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
    # Built directly: `reason` is a WIRE field here (why the decision was replaced) and
    # also the Outcome's own, and the helpers refuse to guess which one you meant.
    return O.Outcome(kind="decision.superseded", data={"id": item, "by": by, "reason": reason})


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
    hits, wide = governing(st, it.globs)
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


def decision_list(
    repo: Path, *, all: bool = False, since: str = "", limit: int | None = None
) -> O.Outcome:
    """Decisions in force; `all` includes the superseded ones.

    `since` keeps those recorded at or after a date/timestamp; `limit` keeps the NEWEST
    N, so the display order (oldest first, the order a decision was made in) never turns
    into a lie about which ones were cut. `total`/`shown` let a surface say what it left
    out, exactly as the viewers do.
    """
    _log, _cfg, st = _load(repo)
    live = [d for d in st.decisions.values() if d.live]
    dead = [d for d in st.decisions.values() if not d.live]
    rows = live + dead if all else live
    # The projected keys exist on EVERY path, refusals included: the MCP tool declares
    # `payload: "rows"`, so `out.body("rows")` runs before a refusal is rendered and raises
    # KeyError -- the wire shape must hold even when the answer is "no" (roborev on fd7ab63a,
    # which caught the `since` branch too).
    data: dict[str, Any] = {
        # `live` is a property, so plain() leaves it out; the human renderer reads it
        # and crashed with KeyError: 'live' (Bb177c2e0e9).
        "rows": [],
        "live": len(live),
        # How many exist but were not shown -- so the human renderer can say so without
        # folding a second time, and a machine caller can tell "none recorded" from
        # "none in force".
        "hidden": 0 if all else len(dead),
        "all": all,
        "total": len(rows),
        "shown": 0,
        "limit": limit or 0,
    }
    if limit is not None and limit < 0:
        # Refused, like the viewer engine's `limit < 1`: a negative cap silently emptying
        # the list would report "no decisions match" for an argument that cannot mean that.
        return O.refused(
            "decision.list",
            f"limit must be 0 (all) or a positive count, got {limit}",
            **data,
        )
    if since:
        try:
            at_or_after = _cutoff(since)
        except ExportError as exc:
            return O.refused("decision.list", str(exc).replace("--since", "since"), **data)
        rows = [d for d in rows if d.at and at_or_after(d.at)]
    rows.sort(key=lambda x: x.at)
    total = len(rows)  # matched, before the limit cut -- what `shown` is short of
    if limit:
        rows = rows[-limit:] if limit > 0 else []
    data["rows"] = [{**_plain(d), "live": d.live} for d in rows]
    data["total"] = total
    data["shown"] = len(rows)
    if not rows:
        # `hidden`/`total` say WHICH emptiness this is: nothing recorded at all, or
        # nothing left after a filter. Reporting the first for the second sends the
        # reader to record a decision they already have.
        if since or limit:
            return O.nothing(
                "decision.list",
                "No architectural decisions match the filters.",
                **data,
            )
        return O.nothing(
            "decision.list",
            "No architectural decisions recorded.\n"
            "  ddflow decision add --title '...' --decision '...' --globs 'src/x/*'",
            **data,
        )
    return O.ok("decision.list", **data)
