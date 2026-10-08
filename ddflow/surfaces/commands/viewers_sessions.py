"""`ddflow session list` and `session show <id>` (read-only, straight from the log)."""

from __future__ import annotations

import sys

from ...api import view_read
from ...core import clock
from ...services import session_view as V
from ..context import NOTHING, OK, REFUSED, Ctx
from ..registry import Command, Param
from ..render import emit_json
from .viewers_search import view_head


def _day(ts: str) -> str:
    return clock.fmt_minute(ts) if ts else "-"


def cmd_session_list(a, c: Ctx) -> int:
    out = view_read(c.repo, "session", state=a.state, owner=a.owner, since=a.since, limit=a.limit)
    # --json is the shape `task|phase|bug|research list --json` prints, so a consumer tells a
    # page from the whole; the filter is echoed under the flag's own name.
    if (done := view_head(c, out)) is not None:
        return done
    d = out.data
    if not d["rows"]:
        which = f" matching {d['filters']}" if d["filters"] else ""
        print(
            f"No sessions{which}. (Prompts recorded with no session are attached by "
            "`ddflow session adopt-orphans`.)"
        )
        return NOTHING
    for r in d["rows"]:
        flags = r["state"] + (", implicit" if r["implicit"] else "")
        span = f"{_day(r['started'])} .. {_day(r['ended']) if r['ended'] else 'now'}"
        print(
            f"  {r['id']:<22.22s} {r['agent']:<14.14s} {span}  "
            f"{r['prompts']}p {r['notes']}n {r['items']} item(s)  [{flags}]"
        )
    if d["truncated"]:
        print(f"\n(showing {len(d['rows'])} of {d['total']}; raise --limit, at most {V.MAX_LIMIT})")
    return OK


def cmd_session_show(a, c: Ctx) -> int:
    try:
        d = V.show_session(c.log.read_all(), c.cfg, a.id)
    except V.SessionViewError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    if c.json:
        emit_json(d)
        return OK
    flags = d["state"] + (", implicit" if d["implicit"] else "")
    print(f"{d['id']}  [{flags}]  agent {d['agent'] or '-'}  model {d['model'] or '-'}")
    print(f"  {_day(d['started'])} .. {_day(d['ended']) if d['ended'] else 'now'}")
    if d["items"]:
        print(f"  items: {', '.join(d['items'])}")
    print()
    for e in d["entries"]:
        tag = f"  [{e['item']}]" if e["item"] else ""
        print(f"{_day(e['at'])}  {e['kind']}{tag}")
        for line in (e["text"] or "").splitlines() or [""]:
            print(f"    {line}")
    if d["summary"]:
        print(f"\nsummary: {d['summary']}")
    return OK


#: `session list` and `session show`: the CLI half of `ddflow_list` for sessions.
COMMANDS = (
    Command(
        path=("session", "list"),
        summary="sessions, newest first: agent, span, items, prompts, state",
        params=(
            Param("state", default="", help="open or ended"),
            Param(
                "owner",
                default="",
                help="only sessions of this agent (not --agent, which is who YOU are)",
            ),
            Param("since", default="", help="ISO date or timestamp: last activity at or after"),
            Param("limit", type="integer", default=V.DEFAULT_LIMIT),
        ),
        handler=cmd_session_list,
        via=("ddflow_list", "session"),
    ),
    Command(
        path=("session", "show"),
        summary="one session: every prompt and note in order, redacted",
        params=(Param("id", positional=True),),
        handler=cmd_session_show,
        via=("ddflow_list", "session"),
    ),
)
