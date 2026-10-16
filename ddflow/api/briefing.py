"""`brief --for-agent`: the standard launch brief of a subagent working one item.

Part of the `brief` command; the text is `services.agent_brief`'s and the template's, this
gathers what the item adds to it (decisions, lessons) from the log."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..core import provenance as PV
from ..core.budget import approx_tokens
from ..infra import worktree as W
from ..infra.store import Store
from ..services import agent_brief as AB
from ..services import prompts as P
from ..services import searchcore as SC
from ..services.guidance import inject as GI
from ..views.markdown import BRIEF_DECISION_CHARS
from ._base import _load


def _decision_lines(cfg, st, item: str) -> tuple[list[str], int]:
    """The decisions that govern the item, one fenced line each (cut as `brief` cuts them),
    and how many there are."""
    found = [d for d in GI.for_item(cfg, st, item).records("decision") if d.id in st.decisions]
    lines = []
    for rec in found:
        d = st.decisions[rec.id]
        text = d.decision
        if len(text) > BRIEF_DECISION_CHARS:
            text = text[:BRIEF_DECISION_CHARS].rsplit(" ", 1)[0] + " …"
        line = "- " + PV.fence("decision", d.id, f"**{d.title}** — {text}", PV.decision_origin(d))
        lines.append(line + (f"  (SUPERSEDED by {d.superseded_by})" if d.superseded_by else ""))
    return lines, len(lines)


def _lesson_lines(cfg, store, st, query: str, count: int) -> list[str]:
    hits = SC.search_table(store, "lessons", query, count) if query and count > 0 else []
    lines = []
    for ls in hits:
        rule = (ls.get("rule") or "")[: cfg.lessons.snippet_chars]
        rec = st.lessons.get(ls.get("id", ""))
        origin = PV.lesson_origin(rec) if rec else PV.Origin(PV.UNKNOWN)
        text = f"**{ls.get('title', '')}** — {rule}"
        lines.append("- " + PV.fence("lesson", ls.get("id", ""), text, origin))
    return lines


def agent_brief(
    repo: Path, *, item: str, for_agent: str, note: str = "", agent: str = ""
) -> O.Outcome:
    """The launch brief of subagent ``for_agent`` for ``item``: the standard text, then the
    item's own part. Exit 1 for an unknown item or an unusable agent name."""
    log, cfg, _loaded = _load(repo, agent)
    store = Store(repo, cfg)
    st = store.ensure(log)
    if not AB.AGENT_NAME.fullmatch(for_agent or ""):
        return O.failed(
            "brief", f"{for_agent!r} is not a usable agent name (letters, digits, . _ -)", text=""
        )
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed("brief", f"no item {item!r}: `ddflow list` shows the ids", text="")
    try:
        tmpl = P.resolve("agent_brief", repo, P.overrides_from(cfg))
        decisions, total = _decision_lines(cfg, st, item)
        facts = AB.Facts(
            agent=for_agent,
            item=item,
            title=it.title,
            body=it.body.strip(),
            globs=list(it.globs),
            needs=list(it.needs),
            base=cfg.worktree.base_ref or W.default_branch(W.repo_root(repo)),
            lease_s=cfg.lease.ttl_s,
            heartbeat_s=cfg.lease.heartbeat_s,
            note=note.strip(),
            extra=cfg.session.agent_brief_extra.strip(),
            decisions=decisions,
            decisions_all=total,
            lessons=_lesson_lines(
                cfg,
                store,
                st,
                f"{it.title} {it.body} {' '.join(it.tags)}",
                cfg.session.agent_brief_lessons,
            ),
        )
        facts.decisions = decisions[: max(0, cfg.session.agent_brief_decisions)]
        got = AB.render(
            tmpl,
            facts,
            off=cfg.session.agent_brief_off,
            max_chars=cfg.session.agent_brief_max_chars,
        )
    except P.TemplateError as exc:
        return O.failed("brief", str(exc), text="")
    return O.ok(
        "brief",
        brief=got["text"],
        text=got["text"],
        item=item,
        for_agent=for_agent,
        sections=got["sections"],
        over=got["over"],
        approx_tokens=approx_tokens(got["text"]),
    )
