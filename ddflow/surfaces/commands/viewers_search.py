"""`ddflow search "<text>"` -- one search across everything the project records.

Rendering only: what matches, in what order, and the redaction of every snippet belong
to `services/search.py`. The parser and handler live here so `cli.py` needs a single
`register` call.
"""

from __future__ import annotations

import json
import sys

from ...services import search as S
from ..context import NOTHING, OK, REFUSED, Ctx


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


def cmd_search(a, c: Ctx) -> int:
    mode = "exact" if a.exact else "regex" if a.regex else "ranked"
    try:
        res = S.search(
            c.store.ensure(c.log),
            c.log.read_all(),
            c.cfg,
            a.query,
            S.Filters(a.kind, a.state, a.phase, a.owner, a.since),
            mode=mode,
            limit=a.limit,
        )
    except S.SearchError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    if c.json:
        body = {
            "record_kind": "search",
            "query": res.query,
            "mode": res.mode,
            "rows": res.rows,
            "total": res.total,
            "shown": len(res.rows),
            "limit": res.limit,
            "truncated": res.truncated,
            "searched": res.searched,
            "filters": {("owner" if k == "agent" else k): v for k, v in res.filters.items()},
            "note": res.note,
        }
        print(json.dumps(body, indent=2, default=str))
        return OK if res.rows else NOTHING
    if not res.rows:
        which = f" under {res.filters}" if res.filters else ""
        what = "No matches" if not res.note else "No matches yet"
        print(f"{what} for {res.query!r} ({res.mode}){which}; searched {res.searched} records.")
        if res.note:
            print(res.note)
        return NOTHING
    for r in res.rows:
        print(
            f"  {r['kind']:<9} {r['id']:<22.22s} {r['state']:<12.12s} {r['date'][:10]:<10}  "
            f"{r['snippet']}"
        )
    if res.truncated:
        print(f"\n(showing {len(res.rows)} of {res.total}; raise --limit, at most {S.MAX_LIMIT})")
    if res.note:
        print(f"\n({res.note})")
    return OK
