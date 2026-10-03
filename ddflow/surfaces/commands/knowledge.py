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
from ...core import provenance as PV
from .. import dedupe_flags as D
from ..context import FAIL, NOTHING, OK, Ctx

#: `event.kind` -> a verb a person reads. Imported from the CLI's table so there is one.
from ..history_verbs import HISTORY_VERBS


def cmd_lesson(a, c: Ctx) -> int:
    if a.lesson_cmd == "add":
        out = D.run(
            a,
            c,
            lambda answer: A.lesson_add(
                c.repo,
                A.LessonDraft(
                    title=a.title,
                    rule=a.rule or "",
                    why=a.why or "",
                    how=a.how or "",
                    summary=a.summary or "",
                    tags=a.tags or "",
                    seen_in=a.seen_in or "",
                    supersedes=a.supersedes or "",
                    pattern=a.pattern or "",
                    globs=a.globs or "",
                    id=a.id or "",
                    answer=answer,
                ),
                agent=c.requested_agent,
            ),
        )
        settled = D.settle(a, c, out)
        if settled is not None:
            return settled
        if out.exit:
            c.out(out.reason, out.body())
            return out.exit
        n = out.data.get("sites", 0)
        note = f" — inventory: {n} site(s) now" if a.pattern else ""
        c.out(f"lesson {out.data['id']} recorded{note}", out.body(("id", "sites", "inventory")))
        return OK
    if a.lesson_cmd == "verify":
        out = A.lessons_verify(c.repo, agent=c.requested_agent)
        c.out(out.data.get("text", "") or out.reason, out.body())
        return out.exit
    if a.lesson_cmd == "search":
        out = A.lesson_search(c.repo, a.query, limit=a.limit, agent=c.requested_agent)
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


def cmd_job(a, c: Ctx) -> int:
    from ...api import jobs as AJ

    if a.job_cmd == "run":
        out = AJ.job_run(
            c.repo,
            a.item,
            a.command,
            log_file=a.log or "",
            cwd=a.cwd or "",
            agent=c.requested_agent,
        )
        msg = (
            out.reason
            if out.exit
            else f"job {out.data['id']} running as pid {out.data['pid']}; log {out.data['log']}"
        )
        c.out(msg, out.body(("id", "pid", "log", "cwd")))
        return out.exit
    if a.job_cmd == "add":
        out = AJ.job_add(
            c.repo,
            a.item,
            a.pid,
            command=a.command or "",
            log_file=a.log or "",
            agent=c.requested_agent,
        )
        c.out(
            out.reason if out.exit else f"job {out.data['id']} registered",
            out.body(("id", "pid", "log", "cwd")),
        )
        return out.exit
    if a.job_cmd == "end":
        out = AJ.job_end(
            c.repo,
            a.job,
            exit_code=a.exit_code,
            note=a.note or "",
            force=a.force,
            agent=c.requested_agent,
        )
        code = out.data.get("exit_code")
        shown = "unknown" if code is None else code
        c.out(
            out.reason if out.exit else f"job {a.job} ended (exit {shown})",
            out.body(("id", "exit_code")),
        )
        return out.exit
    out = AJ.job_list(c.repo, item=a.item or "", include_ended=a.all, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body("jobs"), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for j in out.data["jobs"]:
        print(
            f"{j['id']}  {j['item']}  {j['status'].upper():9}  {j['detail']}\n    {j['command']}  -> {j['log']}"
        )
    return OK


def cmd_memory(a, c: Ctx) -> int:
    if a.memory_cmd == "add":
        out = D.run(
            a,
            c,
            lambda answer: A.memory_add(
                c.repo,
                a.text,
                tags=a.tags or "",
                id=a.id or "",
                answer=answer,
                agent=c.requested_agent,
            ),
        )
        settled = D.settle(a, c, out)
        if settled is not None:
            return settled
        msg = out.reason if out.exit else f"remembered {out.data['id']}"
        c.out(msg, out.body(("id", "replaced")))
        return out.exit
    if a.memory_cmd == "forget":
        out = A.memory_forget(c.repo, a.id, reason=a.reason, agent=c.requested_agent)
        c.out(out.reason if out.exit else f"forgot {a.id}", out.body(("id",)))
        return out.exit
    out = A.memory_list(
        c.repo,
        query=a.query or "",
        limit=a.limit or 0,
        include_forgotten=a.all,
        agent=c.requested_agent,
    )
    if c.json:
        print(json.dumps(out.body(("memories", "total_live")), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for m in out.data["memories"]:
        gone = f"  [FORGOTTEN: {m['forgotten']}]" if m["forgotten"] else ""
        print(f"{m['id']}  {m['at'][:10]}  {m['text']}{gone}")
    return OK


def cmd_recall(a, c: Ctx) -> int:
    """One query across everything the project remembers, printed under a budget."""
    from ...infra.store import summarise_row

    out = A.recall(
        c.repo,
        a.query,
        sources=a.sources or "",
        limit=a.limit,
        max_chars=a.max_chars,
        agent=c.requested_agent,
    )
    if c.json:
        print(json.dumps(out.body("results"), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING

    results = out.data["_render"]["results"]
    budget, used = out.data["max_chars"], 0
    # BEFORE the records, so a recall cut short by the budget still carried it.
    print(PV.DATA_RULE)
    used += len(PV.DATA_RULE)
    for table, label, why in out.data["_render"]["sources"]:
        rows = results.get(table)
        if not rows:
            continue
        header = f"\n## {label}  — {why}\n"
        print(header, end="")
        used += len(header)
        for r in rows:
            head, body = summarise_row(table, r)
            block = _recall_block(table, r, head, body)
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


def _recall_block(table: str, r: dict, head: str, body: str) -> str:
    """One hit. A decision, lesson, memory or recorded prompt/note is somebody's words:
    it carries who recorded it and is fenced as data (`core/provenance.py`). A hit the
    index holds but the state cannot name is `unknown`, not unlabelled."""
    if table not in PV.TABLE_KIND:
        return f"  [{r.get('id', '?')}] {head}\n" + (f"      {body}\n" if body else "")
    prov = r.get("provenance")
    if prov:
        origin = PV.Origin(
            prov.get("trust", PV.UNKNOWN), prov.get("by", ""), prov.get("source", "")
        )
    else:
        # A prompt or note is recorded by an agent, whoever's words it quotes.
        origin = PV.Origin(PV.AGENT if table == "prompts" else PV.UNKNOWN)
    kind = "note" if r.get("role") == "note" else PV.TABLE_KIND[table]
    text = head + (f"\n{body}" if body else "")
    fenced = PV.fence(kind, r.get("id", ""), text, origin, inline=False)
    return f"  [{r.get('id', '?')}] ({origin.label()})\n      {fenced}\n"


def cmd_similar(a, c: Ctx) -> int:
    """Existing records like a text, before it is filed. Exit 0 with candidates, 2 with none."""
    out = A.similar(c.repo, a.text, kinds=a.kind or "", agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body("candidates"), indent=2, default=str))
        return out.exit
    if out.exit != OK:
        print(out.reason)
        return out.exit
    print("\n".join(D.candidate_lines(out.data["candidates"])))
    return OK


def cmd_research(a, c: Ctx) -> int:
    out = D.run(
        a,
        c,
        lambda answer: A.research_add(
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
    c.out(
        f"research {out.data['id']} recorded ({out.data['verdict']})",
        out.body(("id", "verdict")),
    )
    return OK


def cmd_bug(a, c: Ctx) -> int:
    if a.bug_cmd == "found":
        out = D.run(
            a,
            c,
            lambda answer: A.bug_found(
                c.repo,
                summary=a.summary,
                item=a.item or "",
                id=a.id or "",
                title=a.title,
                severity=a.severity,
                scope=a.scope,
                answer=answer,
                agent=c.requested_agent,
            ),
        )
        settled = D.settle(a, c, out)
        if settled is not None:
            return settled
        if out.exit:
            print(out.reason, file=sys.stderr)
            return out.exit
        closed = out.data.get("resolution", "")
        note = f" -- already closed as {closed}; this report does not reopen it" if closed else ""
        offer = out.data.get("offer", "")
        c.out(
            f"bug {out.data['id']} recorded{note}" + (f"\n{offer}" if offer else ""),
            out.body(("id", "offer")),
        )
        return OK
    if a.bug_cmd == "invalid":
        out = A.bug_invalid(
            c.repo,
            a.id,
            reason=a.reason or "",
            evidence=a.evidence or "",
            agent=c.requested_agent,
        )
        if out.exit != OK:
            print(out.reason, file=sys.stderr)
            return out.exit
        probe = f" (evidence: {a.evidence})" if a.evidence else ""
        c.out(
            f"bug {a.id} closed as invalid: {out.data['invalid_reason']}{probe}",
            out.body(("id", "invalid_reason", "evidence", "unchecked")),
        )
        return OK
    out = A.bug_fixed(
        c.repo,
        a.id,
        regression_test=a.regression_test or [],
        lesson=a.lesson or "",
        lesson_title=getattr(a, "lesson_title", "") or "",
        lesson_rule=getattr(a, "lesson_rule", "") or "",
        changelog=getattr(a, "changelog", "") or "",
        agent=c.requested_agent,
    )
    # Every non-OK exit, not just 1: checking only FAIL let a REFUSED unknown id print
    # "bug NOPE closed" and exit 0 while nothing was appended (B-cli-bugfixed-refusal-ok).
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    c.out(f"bug {a.id} closed (regression: {out.data['regression_test']})", out.body(("id",)))
    return OK


def _misread_hint(a) -> str:
    """The positional is the SESSION id; prose there means the words went in the wrong slot."""
    if any(ch.isspace() for ch in a.session):
        return (
            f"\nthe first argument is the SESSION id, not the text -- got {a.session!r}. "
            f"Pass the words with --text: "
            f'ddflow session {a.session_cmd} <session-id> --text "..."'
        )
    return ""


def _session_text(a) -> str | None:
    """The words for `session prompt`/`note`: `--text`, else piped stdin.

    stdin because the operator's prompt is frequently multi-line and frequently contains
    the characters a shell would eat. A terminal on stdin is a person, not a pipe, and
    reading it waits for them forever -- so that is refused before reading (None).
    """
    if a.text is not None:
        return a.text
    if sys.stdin is None or sys.stdin.isatty():
        print(
            f"refusing to read the {a.session_cmd} text from a terminal: pass it with --text "
            f"(or pipe it on stdin). The positional argument is the session id, not the text."
            + _misread_hint(a),
            file=sys.stderr,
        )
        return None
    return sys.stdin.read()


def _where(d: dict) -> str:
    """Which session took the words, said when the caller did not name one."""
    how = d.get("how", "explicit")
    if how == "off":
        return " -- NOT recorded: session.log_prompts is off"
    if how == "explicit":
        return ""
    label = {"implicit": "implicit, new", "harness": "harness"}.get(how, how)
    return f" in session {d['session']} ({label})"


def cmd_session(a, c: Ctx) -> int:
    if a.session_cmd == "start":
        out = A.session_start(
            c.repo, model=a.model or "", tool=a.tool or "", agent=c.requested_agent
        )
        c.out(out.data["session"], out.body(("session",)))
        return OK
    if a.session_cmd in ("prompt", "note"):
        text = _session_text(a)
        if text is None:
            return FAIL
        if a.session_cmd == "prompt":
            out = A.session_prompt(
                c.repo, a.session, text, item=a.item or "", agent=c.requested_agent
            )
        else:
            out = A.session_note(
                c.repo, a.session, text, item=a.item or "", agent=c.requested_agent
            )
        if out.exit != OK:
            print(out.reason + _misread_hint(a), file=sys.stderr)
            return out.exit
        where = _where(out.data)
        if where.startswith(" -- NOT"):
            c.out(f"not recorded:{where[len(' -- NOT recorded:') :]}", out.body(("session", "how")))
        elif a.session_cmd == "prompt":
            c.out(
                f"recorded ({out.data['redactions']} redaction(s)){where}",
                out.body(("redactions", "session", "how")),
            )
        else:
            c.out(f"noted{where}", out.body(("session", "how")))
        return OK
    if a.session_cmd == "adopt-orphans":
        out = A.session_adopt_orphans(c.repo, agent=c.requested_agent)
        if out.exit != OK:
            print(out.reason, file=sys.stderr)
            return out.exit
        c.out(f"adopted {out.data['adopted']} orphan(s)", out.body(("adopted",)))
        return OK
    if a.session_cmd == "end":
        A.session_end(c.repo, a.session, summary=a.summary or "", agent=c.requested_agent)
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
    return (
        f"  {ev.ts[:16].replace('T', ' ')}  {ev.agent:<14.14s} "
        f"{ev.subject:<22.22s} {verb:<22s} {detail}"
    )


#: a payload string longer than this is cut in `--json`, with a note saying so.
_JSON_FIELD_CAP = 500


def _bounded_events(events: list[dict]) -> list[dict]:
    """Copies of the events with any oversized string in `data` truncated, marked
    `truncated: true` on the event so a reader knows the payload is not whole."""
    out = []
    for e in events:
        cut = False
        data = {}
        for k, v in (e.get("data") or {}).items():
            if isinstance(v, str) and len(v) > _JSON_FIELD_CAP:
                data[k] = f"{v[:_JSON_FIELD_CAP]}... [truncated {len(v) - _JSON_FIELD_CAP} chars]"
                cut = True
            else:
                data[k] = v
        out.append({**e, "data": data, **({"truncated": True} if cut else {})})
    return out


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
        agent=c.requested_agent,
        by_agent=getattr(a, "log_agent", "") or "",
        tail=getattr(a, "tail", 0) or 0,
    )
    if c.json:
        body = out.body(("total", "shown", "events"))
        body["events"] = _bounded_events(body.get("events") or [])
        print(json.dumps(body, indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    shown, total = out.data["_render"]["events"], out.data["total"]
    if getattr(a, "tail", 0):
        print(f"{total} event(s); last {len(shown)}, oldest first:\n")
    else:
        print(f"{total} event(s); newest {len(shown)} first:\n")
    for e in shown:
        print(_history_line(e))
    if total > len(shown):
        print(f"\n  ... {total - len(shown)} older. --limit/--tail to see more.")
    print(
        "\n  Ordered by Lamport clock, not wall time: two agents have two clocks, and "
        "\n  sorting a merged history by timestamp interleaves them wrongly."
    )
    return OK
