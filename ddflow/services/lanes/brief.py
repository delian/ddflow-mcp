"""A lane's brief: what its coordinator would hand-write as the lane's whole first prompt.

Assembled from the log alone: the item it holds (or last held), the lease and worktree, its
last session's summary and notes. Every text a lane or an agent wrote is FENCED as data
(`core.provenance.fence`), never quoted bare. When the dispatch records of B-am-capture and
the 'Previous run' builder of B-am-restart-brief land, `_previous_run` is the one place that
defers to them.
"""

from __future__ import annotations

from collections.abc import Sequence

import ddflow.services.lanes.states as S

from ...config import Config
from ...core import provenance as P
from ...core.records import Item, State
from ...core.textcut import clip
from .ledger import Lane
from .safe import safe


def _text(cfg: Config, kind: str, ident: str, text: str, by: str) -> str:
    cut = clip(text.strip(), cfg.lanes.brief_text_chars, marker=" ...", boundary="word")
    return P.fence(kind, ident, cut, P.Origin(P.AGENT, by=by), inline=False)


def _previous_run(lane: Lane, st: State, cfg: Config) -> list[str]:
    sess = st.sessions.get(lane.session)
    if sess is None:
        return ["No earlier session of this lane is recorded."]
    out = [
        f"Session {safe(sess.id)} ({safe(sess.model) or 'model unknown'}), "
        f"started {safe(sess.started_at[:16])}."
    ]
    if sess.summary:
        out.append(_text(cfg, "session-summary", sess.id, sess.summary, lane.name))
    notes = sess.notes[-cfg.lanes.brief_notes :] if cfg.lanes.brief_notes > 0 else []
    out += [
        _text(cfg, "session-note", f"{sess.id}#{n['seq']}", n["text"], lane.name) for n in notes
    ]
    if not sess.summary and not notes:
        out.append("It left no summary or notes.")
    return out


def _assignment(it: Item, cfg: Config) -> list[str]:
    """The item's own fields, as data: a tag, a dependency or a glob is text somebody typed."""
    rows = [f"item: {it.id} ({it.kind}, {it.state})"]
    for label, value in (
        ("phase", it.parent),
        ("tags", ", ".join(it.tags)),
        ("needs", ", ".join(it.needs)),
        ("writes only", ", ".join(it.globs)),
    ):
        if value:
            rows.append(f"{label}: {value}")
    return [_text(cfg, "item-fields", it.id, "\n".join(rows), it.id)]


def _lease(lane: Lane, it: Item | None) -> list[str]:
    if it is None or it.lease is None:
        return ["You hold no lease."]
    rows = [f"- lease: held by {safe(it.lease.holder)} on {safe(it.id)}"]
    if lane.worktree:
        rows.append(f"- worktree: {safe(lane.worktree)} (work ONLY there)")
    if it.branch:
        rows.append(f"- branch: {safe(it.branch)}")
    return rows


def _next_steps(lane: Lane, it: Item | None) -> list[str]:
    if it is not None and lane.state in S.ACTIVE + S.UNFINISHED:
        item = safe(it.id)
        return [
            f"Run `ddflow brief` first, then `ddflow show {item}`.",
            f"Resume the item in its worktree; `ddflow gate status {item}` says which "
            f"gate is next. Re-claim it if your lease lapsed (`ddflow claim {item}`).",
        ]
    if lane.ready:
        return [
            f"Run `ddflow brief`, then claim a ready item: {', '.join(safe(i) for i in lane.ready[:5])}."
        ]
    return ["Run `ddflow brief`; nothing is ready for you now, `ddflow wait` for work."]


def lane_brief(lane: Lane, st: State, cfg: Config, others: Sequence[Lane] = ()) -> str:
    """The brief for ``lane`` as one text, usable as its entire first prompt."""
    it = st.items.get(lane.item)
    held_by_others = [safe(o.name) for o in others if o.name != lane.name and o.state in S.ACTIVE]
    lines = [
        f"# Lane brief: {safe(lane.name)}",
        "",
        f"You are lane {safe(lane.name)}"
        + (f" on {safe(lane.harness)}" if lane.harness else "")
        + ". Pass this name as your identity on every ddflow call: `--agent NAME` on the command line, ddflow_identify over MCP.",
        P.DATA_RULE,
        "",
        f"State when this brief was cut: {lane.state}"
        + (f" ({safe(lane.detail)})" if lane.detail else "")
        + ".",
        "",
        "## Objective",
    ]
    if it is None:
        lines.append("No item is recorded for this lane.")
    else:
        lines += [
            _text(cfg, "item-title", it.id, it.title, it.id),
            "",
            _text(cfg, "item", it.id, it.body, it.id),
            "",
            "## Assignment",
        ]
        lines += [*_assignment(it, cfg), "", "## Lease and worktree", *_lease(lane, it)]
    lines += ["", "## Previous run", *_previous_run(lane, st, cfg)]
    if held_by_others:
        lines += ["", "## Other lanes working now", ", ".join(sorted(held_by_others))]
    lines += ["", "## Next", *_next_steps(lane, it)]
    return "\n".join(lines) + "\n"
