"""`ddflow rule ...` -- the human surface for the project-rules API.

Same calls and the same wire bodies as the `ddflow_rule_*` MCP tools: the tool declares
its payload projection and both surfaces ask the Outcome for it.
"""

from __future__ import annotations

import sys

from ...api import (
    Rule,
    RuleDedupAnswer,
    rule_add,
    rule_dedup_check_dry_run,
    rule_get,
    rule_list,
    rule_remove,
    rule_search,
    rule_update,
)
from ...core.outcome import EXIT_NAMES, REFUSED
from ..context import FAIL, Ctx
from ..render import emit_json

_PAYLOADS = {
    "list": ("rows", "count"),
    "search": ("rows", "count", "query"),
    "add": (
        "id",
        "candidates",
        "related",
        "options",
        "extended",
        "extended_kind",
        "relation",
        "dedupe_unavailable",
        "unrecorded",
    ),
    "edit": ("id", "candidates", "related", "options", "dedupe_unavailable", "unrecorded"),
    "remove": ("id", "unrecorded"),
    "show": ("id", "title", "content", "tags", "scope", "priority", "globs", "created", "updated"),
}


def _csv(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def _check_note(out, done: str = "filed") -> str:
    """What a filed or edited rule's result says about the duplicate check: that it could
    not run (``done`` UNCHECKED), or the records it reads like (a warning)."""
    if why := out.data.get("dedupe_unavailable"):
        return f"\n  {done} UNCHECKED: the duplicate check could not run ({why})"
    shown = [c for c in out.data.get("candidates", []) if c.get("kind") != "rule"]
    if not shown:
        return ""
    return "\n  It reads like: " + "; ".join(
        f"{c['id']} ({c.get('kind', '')}, score {c['score']:.2f})" for c in shown
    )


def _agent(c: Ctx) -> str:
    return c.log.agent_id if getattr(c, "log", None) else ""


def _answer(a) -> RuleDedupAnswer | None:
    if getattr(a, "new", False):
        return RuleDedupAnswer("new", "")
    for rel in ("extends", "duplicate_of", "related"):
        if getattr(a, rel, ""):
            return RuleDedupAnswer(rel, getattr(a, rel))
    return None


def _emit(c: Ctx, out, verb: str, human: str) -> int:
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        body = out.body(_PAYLOADS[verb])
        if out.exit == REFUSED and isinstance(body, dict):
            # The same lead the MCP tool puts first on a refusal: the reason, then the body.
            lead = {"reason": out.reason, "outcome": EXIT_NAMES[REFUSED], "exit": REFUSED}
            body = {"refusal": lead, **body}
        emit_json(body)
    elif out.exit == REFUSED:
        # `human` is the success sentence ("added rule r-x"); a refusal says why instead.
        print(out.reason, file=sys.stderr)
    else:
        print(human or out.reason)
    if (note := out.data.get("unrecorded")) and not c.json:
        print(f"warning: {note}", file=sys.stderr)
    return out.exit


def _rule_line(r: dict) -> str:
    return f"{r['id']}  [{r.get('scope', '')}] {r.get('title', '')}"


def _cmd_edit(a, c: Ctx) -> int:
    verb = "edit"
    fields: dict = {}
    for name in ("title", "content", "scope"):
        if getattr(a, name) is not None:
            fields[name] = getattr(a, name)
    if a.tags is not None:
        fields["tags"] = _csv(a.tags)
    if a.globs is not None:
        fields["globs"] = _csv(a.globs)
    if a.priority is not None:
        fields["priority"] = a.priority
    out = rule_update(c.repo, a.id, dedup_answer=_answer(a), agent=_agent(c), **fields)
    if out.exit != 0 and out.data.get("candidates"):
        print(out.reason, file=sys.stderr)
        return _emit(c, out, verb, "") if c.json else out.exit
    related = out.data.get("related")
    return _emit(
        c,
        out,
        verb,
        f"updated {a.id}: {', '.join(sorted(fields)) or 'nothing'}"
        + (f" (related to {related})" if related else "")
        + _check_note(out, "edited"),
    )


def cmd_rule(a, c: Ctx) -> int:
    verb = a.rule_cmd or "list"
    if verb == "list":
        out = rule_list(c.repo, tag=a.tag or None, scope=a.scope or None)
        rows = out.data.get("rows", [])
        return _emit(c, out, verb, "\n".join(_rule_line(r) for r in rows))
    if verb == "search":
        out = rule_search(
            c.repo,
            a.query,
            limit=a.limit,
            exact=a.exact,
            regex=a.regex,
            tag=a.tag or None,
            scope=a.scope or None,
        )
        return _emit(c, out, verb, "\n".join(_rule_line(r) for r in out.data.get("rows", [])))
    if verb == "show":
        out = rule_get(c.repo, a.id)
        d = out.data
        human = (
            f"{d.get('id')}  {d.get('title')}\n  scope {d.get('scope')}  priority "
            f"{d.get('priority')}  tags {d.get('tags')}  globs {d.get('globs')}\n\n"
            f"{d.get('content', '')}"
        )
        return _emit(c, out, verb, human)
    if verb == "remove":
        out = rule_remove(c.repo, a.id, agent=_agent(c))
        return _emit(c, out, verb, f"removed {a.id}")
    if verb == "edit":
        return _cmd_edit(a, c)
    if a.check:
        out = rule_dedup_check_dry_run(
            c.repo, a.content or "", title=a.title or "", rule_id=a.id, agent=_agent(c)
        )
        return _emit(c, out, verb, "\n".join(str(x) for x in out.data.get("candidates", [])))
    out = rule_add(
        c.repo,
        Rule(
            id=a.id,
            title=a.title,
            content=a.content or "",
            tags=_csv(a.tags or ""),
            scope=a.scope or "project",
            priority=a.priority if a.priority is not None else 50,
            globs=_csv(a.globs or ""),
        ),
        agent=_agent(c),
        dedup_answer=_answer(a),
    )
    if out.exit != 0 and out.data.get("candidates"):
        print(out.reason, file=sys.stderr)
        if c.json:
            return _emit(c, out, "add", "")
        return out.exit
    if out.data.get("extended"):
        return _emit(
            c,
            out,
            verb,
            f"rule text added to {out.data['extended']} ({out.data.get('extended_kind', '')}); "
            f"no rule {a.id} filed",
        )
    said = {"related": "related to", "duplicate_of": "duplicate of", "extends": "extends"}
    linked = next((f" ({said[r]} {out.data[r]})" for r in said if out.data.get(r)), "")
    return _emit(c, out, verb, f"added rule {a.id}{linked}{_check_note(out)}")
