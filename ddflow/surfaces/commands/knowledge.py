"""`lesson`, `recall`, `research`, `bug`, `session`, `history` — the human surface for
`api.knowledge`.

`recall`'s renderer is the substantial one: it prints under a character BUDGET, because
the output goes into an agent's context and an unbounded dump of everything the project
remembers is not a recall, it is a denial of service against the thing you were about to
think about.
"""

from __future__ import annotations

import json
import sys

from ...api import knowledge as A
from ..context import FAIL, NOTHING, OK, Ctx

#: `event.kind` -> a verb a person reads. Imported from the CLI's table so there is one.
from ..history_verbs import HISTORY_VERBS


def cmd_lesson(a, c: Ctx) -> int:
    if a.lesson_cmd == "add":
        out = A.lesson_add(
            c.repo,
            title=a.title,
            rule=a.rule or "",
            why=a.why or "",
            how=a.how or "",
            tags=a.tags or "",
            seen_in=a.seen_in or "",
            supersedes=a.supersedes or "",
            id=a.id or "",
            agent=c.cfg.agent.id,
        )
        c.out(f"lesson {out.data['id']} recorded", out.body(("id",)))
        return OK
    if a.lesson_cmd == "search":
        out = A.lesson_search(c.repo, a.query, limit=a.limit, agent=c.cfg.agent.id)
        if c.json:
            print(json.dumps(out.body("hits"), indent=2, default=str))
            return out.exit
        if out.exit == NOTHING:
            print(out.reason)
            return NOTHING
        for h in out.data["hits"]:
            print(f"- {h['title']}\n    {(h.get('rule') or '')[: out.data['snippet_chars']]}")
        return OK
    return FAIL


def cmd_recall(a, c: Ctx) -> int:
    """One query across everything the project remembers, printed under a budget."""
    from ...infra.store import summarise_row

    out = A.recall(
        c.repo,
        a.query,
        sources=a.sources or "",
        limit=a.limit,
        max_chars=a.max_chars,
        agent=c.cfg.agent.id,
    )
    if c.json:
        print(json.dumps(out.body("results"), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING

    results = out.data["_render"]["results"]
    budget, used = out.data["max_chars"], 0
    for table, label, why in out.data["_render"]["sources"]:
        rows = results.get(table)
        if not rows:
            continue
        header = f"\n## {label}  — {why}\n"
        print(header, end="")
        used += len(header)
        for r in rows:
            head, body = summarise_row(table, r)
            block = f"  [{r.get('id', '?')}] {head}\n" + (f"      {body}\n" if body else "")
            if used + len(block) > budget:
                print(f"      … truncated at {budget} chars (--max-chars to raise)")
                return OK
            print(block, end="")
            used += len(block)
    print(
        "\nRecall is a prompt to CHECK, not a verdict. A decision above is binding "
        "unless the operator says otherwise; a lesson is advice; a past prompt is "
        "context."
    )
    return OK


def cmd_research(a, c: Ctx) -> int:
    out = A.research_add(
        c.repo,
        A.Finding(
            question=a.question,
            verdict=a.verdict,
            claim=a.claim or "",
            mechanism=a.mechanism or "",
            falsifier=a.falsifier or "",
            probe=a.probe or "",
            probe_output=a.probe_output or "",
            sources=a.sources or "",
            budget=a.budget or "",
            item=a.item or "",
            id=a.id or "",
        ),
        agent=c.cfg.agent.id,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(
        f"research {out.data['id']} recorded ({out.data['verdict']})",
        out.body(("id", "verdict")),
    )
    return OK


def cmd_bug(a, c: Ctx) -> int:
    if a.bug_cmd == "found":
        out = A.bug_found(
            c.repo, summary=a.summary, item=a.item or "", id=a.id or "", agent=c.cfg.agent.id
        )
        c.out(f"bug {out.data['id']} recorded", out.body(("id",)))
        return OK
    out = A.bug_fixed(
        c.repo,
        a.id,
        regression_test=a.regression_test or "",
        lesson=a.lesson or "",
        lesson_title=getattr(a, "lesson_title", "") or "",
        lesson_rule=getattr(a, "lesson_rule", "") or "",
        agent=c.cfg.agent.id,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(f"bug {a.id} closed (regression: {a.regression_test})", out.body(("id",)))
    return OK


def cmd_session(a, c: Ctx) -> int:
    if a.session_cmd == "start":
        out = A.session_start(c.repo, model=a.model or "", tool=a.tool or "", agent=c.cfg.agent.id)
        c.out(out.data["session"], out.body(("session",)))
        return OK
    if a.session_cmd == "prompt":
        # stdin when no `--text`: the operator's prompt is frequently multi-line and
        # frequently contains the characters a shell would eat.
        text = a.text if a.text is not None else sys.stdin.read()
        out = A.session_prompt(c.repo, a.session, text, item=a.item or "", agent=c.cfg.agent.id)
        c.out(f"recorded ({out.data['redactions']} redaction(s))", out.body(("redactions",)))
        return OK
    if a.session_cmd == "note":
        A.session_note(
            c.repo,
            a.session,
            a.text or sys.stdin.read(),
            item=a.item or "",
            agent=c.cfg.agent.id,
        )
        c.out("noted", {})
        return OK
    if a.session_cmd == "end":
        A.session_end(c.repo, a.session, summary=a.summary or "", agent=c.cfg.agent.id)
        c.out("ended", {})
        return OK
    return FAIL


def _history_line(ev) -> str:
    """One event, as a line someone can read."""
    verb = HISTORY_VERBS.get(ev.kind, ev.kind)
    d = ev.data or {}
    detail = (
        d.get("title")
        or d.get("text")
        or d.get("summary")
        or d.get("question")
        or d.get("reason")
        or d.get("note")
        or ""
    )
    if ev.kind == "gate.recorded":
        detail = f"{d.get('gate', '?')} = {d.get('outcome', '?')}"
    elif ev.kind == "lease.acquired":
        detail = f"by {d.get('holder', '?')}"
    elif ev.kind == "item.completed" and d.get("sha"):
        detail = f"as {d['sha'][:8]}"
    detail = " ".join(str(detail).split())[:88]
    return f"  {ev.ts[:16].replace('T', ' ')}  {ev.subject:<22.22s} {verb:<22s} {detail}"


def cmd_history(a, c: Ctx) -> int:
    """One reverse-chronological timeline of everything that happened.

    `status`, `progress`, `replay` and `recall` each answer part of "what has happened
    here", and an operator asking that question had to know which to run. This is the
    plain answer: the log, newest first, filterable.
    """
    out = A.history(
        c.repo,
        item=a.item or "",
        kind=a.kind or "",
        since=a.since or "",
        limit=a.limit,
        agent=c.cfg.agent.id,
    )
    if c.json:
        print(json.dumps(out.body(("total", "shown", "events")), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    shown, total = out.data["_render"]["events"], out.data["total"]
    print(f"{total} event(s); newest {len(shown)} first:\n")
    for e in shown:
        print(_history_line(e))
    if total > len(shown):
        print(f"\n  ... {total - len(shown)} older. --limit to see more.")
    print(
        "\n  Ordered by Lamport clock, not wall time: two agents have two clocks, and "
        "\n  sorting a merged history by timestamp interleaves them wrongly."
    )
    return OK
