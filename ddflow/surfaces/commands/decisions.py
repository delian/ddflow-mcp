"""`ddflow decision ...` — the human surface for `api.decisions`.

Every function here RENDERS an `Outcome`; none of them decides anything. That split is
the B37 shape: one description of a result, two presentations derived from it, rather
than two presentations kept in step by hand. The `--json` bodies are the contract
`tests/test_api_layer.py::MIGRATED_WIRE_SHAPES` pins against the MCP tool.
"""

from __future__ import annotations

import json
import sys

from ...api import decisions as A
from .. import dedupe_flags as D
from ..context import FAIL, NOTHING, OK, Ctx


def _emit(c: Ctx, out, payload: str) -> int:
    """The JSON half, shared. `payload` names the key whose value IS the wire body —
    the same key the MCP tool declares, so the two surfaces cannot drift apart."""
    print(json.dumps(out.data[payload], indent=2, default=str))
    return out.exit


def _decision_add(a, c: Ctx, st) -> int:
    out = D.run(
        a,
        c,
        lambda answer: A.decision_add(
            c.repo,
            A.Draft(
                title=a.title,
                decision=a.decision or "",
                id=a.id or "",
                context=a.context or "",
                consequences=a.consequences or "",
                alternatives=a.alternatives or "",
                globs=a.globs or "",
                tags=a.tags or "",
                sources=a.sources or "",
                status=a.status,
                by=a.by or "",
                item=a.item or "",
                supersedes=a.supersedes or "",
                answer=answer,
            ),
            agent=c.requested_agent,
        ),
    )
    settled = D.settle(a, c, out)
    if settled is not None:
        return settled
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    extra = f"; supersedes {a.supersedes}" if out.data["supersedes"] else ""
    if out.data["ungoverned"]:
        extra += (
            "\n  NOTE: no --globs, so this decision cannot be surfaced automatically "
            "to an agent working the code it governs. It will only be found by search."
        )
    c.out(
        f"decision {out.data['id']} recorded ({out.data['status']}){extra}", {"id": out.data["id"]}
    )
    return OK


def _decision_supersede(a, c: Ctx, st) -> int:
    out = A.decision_supersede(
        c.repo, a.id, by=a.by or "", reason=a.reason or "", agent=c.requested_agent
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(
        f"{out.data['id']} superseded by {out.data['by']}",
        {"id": out.data["id"], "by": out.data["by"]},
    )
    return OK


def _decision_show(a, c: Ctx, st) -> int:
    out = A.decision_show(c.repo, a.id)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        return _emit(c, out, "decision")
    d = out.data["decision"]
    head = f"{d['id']} — {d['title']}\n  status {d['status']}"
    if d.get("superseded_by"):
        head += f" (superseded by {d['superseded_by']})"
    if d.get("decided_by"):
        head += f" · decided by {d['decided_by']}"
    print(head)
    for label, key in (
        ("Context", "context"),
        ("Decision", "decision"),
        ("Consequences", "consequences"),
        ("Alternatives rejected", "alternatives"),
    ):
        if d.get(key):
            print(f"\n{label}:\n  {d[key]}")
    if d.get("globs"):
        print(f"\nGoverns: {', '.join(d['globs'])}")
    return OK


def _decision_applicable(a, c: Ctx, st) -> int:
    out = A.decision_applicable(c.repo, a.id)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        # The object with both lists, unchanged: `{"applicable": [...],
        # "project_wide": [...]}`. Callers index both keys.
        print(
            json.dumps(
                {"applicable": out.data["applicable"], "project_wide": out.data["project_wide"]},
                indent=2,
                default=str,
            )
        )
        return OK if out.exit == OK else NOTHING
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for d in out.data["applicable"]:
        print(f"  [{d['id']}] {d['title']}\n      {d['decision']}")
    for d in out.data["project_wide"]:
        print(f"  [{d['id']}] {d['title']}  (project-wide)\n      {d['decision']}")
    return OK


def _decision_search(a, c: Ctx, st) -> int:
    out = A.decision_search(c.repo, a.query, limit=a.limit)
    if c.json:
        return _emit(c, out, "hits")
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for h in out.data["hits"]:
        print(f"  [{h['id']}] {h['title']}\n      {(h.get('decision') or '')[:200]}")
    return OK


def _decision_list(a, c: Ctx, st) -> int:
    out = A.decision_list(
        c.repo,
        all=bool(getattr(a, "all", False)),
        since=getattr(a, "since", "") or "",
        limit=getattr(a, "limit", None),
    )
    if c.json:
        if out.exit not in (OK, NOTHING):
            # A bad --since is a refusal carrying no `rows`; the reason goes to stderr so
            # `--json` does not crash reading a data key it was never given.
            print(out.reason, file=sys.stderr)
            return out.exit
        return _emit(c, out, "rows")
    if out.exit != OK:
        print(out.reason, file=sys.stdout if out.exit == NOTHING else sys.stderr)
        return out.exit
    for d in out.data["rows"]:
        flag = ""
        if not d["live"]:
            flag = f"  [{d['status']}"
            flag += f" -> {d['superseded_by']}]" if d.get("superseded_by") else "]"
        print(f"  {d['id']:<14} {d['title']}{flag}")
        if d.get("globs"):
            print(f"                 governs {', '.join(d['globs'])}")
    if out.data["hidden"]:
        print(
            f"\n({out.data['hidden']} superseded; --all to include them — the history of how "
            f"the architecture got here is kept, never deleted)"
        )
    if out.data["total"] > out.data["shown"]:
        print(f"\n(showing {out.data['shown']} of {out.data['total']}; raise --limit)")
    return OK


def cmd_decision(a, c: Ctx) -> int:
    """Architectural decisions: record them, consult them, supersede them.

    A thin dispatcher. Each subcommand is its own function because they share nothing
    but the loaded state, and reading them interleaved obscured that.
    """
    st = c.store.ensure(c.log)
    return {
        "add": _decision_add,
        "supersede": _decision_supersede,
        "show": _decision_show,
        "applicable": _decision_applicable,
        "search": _decision_search,
        "list": _decision_list,
    }.get(getattr(a, "decision_cmd", "") or "list", _decision_list)(a, c, st)
