"""`ddflow search "<text>"` -- one search across everything the project records.

Rendering only: what matches, in what order, and the redaction of every snippet belong
to `services/search.py`. The parser and handler live here so `cli.py` needs a single
`register` call.
"""

from __future__ import annotations

import sys

from ...api import view_read
from ...services import search as S
from ..context import NOTHING, OK, REFUSED, Ctx
from ..render import emit_json


def register(s) -> None:
    p = s.add_parser(
        "search",
        help="search tasks, phases, bugs, research, decisions, lessons, sessions, prompts "
        "and the log (exit 2 = no match)",
    )
    p.add_argument("query", help="the text to look for")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--exact", action="store_true", help="case-insensitive substring, not ranked")
    mode.add_argument(
        "--regex", action="store_true", help="a regular expression (unsafe ones are refused)"
    )
    p.add_argument(
        "--kind",
        default="",
        help=f"comma-separated sources to search (default all): {', '.join(S.SOURCES)}",
    )
    p.add_argument("--state", default="", help="only hits in this state")
    p.add_argument("--phase", default="", help="only hits under this phase id")
    p.add_argument(
        "--owner",
        default="",
        help="only hits by this agent (lease holder, session or event agent); "
        "not --agent, which is who YOU are",
    )
    p.add_argument("--since", default="", help="only hits dated at or after this ISO date")
    p.add_argument("--limit", type=int, default=S.DEFAULT_LIMIT, help="most hits to show")
    p.set_defaults(fn=cmd_search)


def view_head(c: Ctx, out) -> int | None:
    """What `search` and `session list` do first with a `view_read` answer: a refusal is
    its reason on stderr, and --json is the body whole (the exit says whether rows came back).
    None when the human rendering goes on."""
    if out.exit == REFUSED:
        print(out.reason, file=sys.stderr)
        return REFUSED
    if c.json:
        emit_json(out.data)
        return OK if out.data["rows"] else NOTHING
    return None


def cmd_search(a, c: Ctx) -> int:
    mode = "exact" if a.exact else "regex" if a.regex else "ranked"
    out = view_read(
        c.repo,
        "search",
        query=a.query,
        mode=mode,
        sources=a.kind,
        state=a.state,
        phase=a.phase,
        owner=a.owner,
        since=a.since,
        limit=a.limit,
    )
    if (done := view_head(c, out)) is not None:
        return done
    d = out.data
    if not d["rows"]:
        which = f" under {d['filters']}" if d["filters"] else ""
        what = "No matches" if not d["note"] else "No matches yet"
        print(f"{what} for {d['query']!r} ({d['mode']}){which}; searched {d['searched']} records.")
        if d["note"]:
            print(d["note"])
        return NOTHING
    for r in d["rows"]:
        print(
            f"  {r['kind']:<9} {r['id']:<22.22s} {r['state']:<12.12s} {r['date'][:10]:<10}  "
            f"{r['snippet']}"
        )
    if d["truncated"]:
        print(f"\n(showing {len(d['rows'])} of {d['total']}; raise --limit, at most {S.MAX_LIMIT})")
    if d["note"]:
        print(f"\n({d['note']})")
    return OK
