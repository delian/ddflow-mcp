"""`ddflow task|phase|bug|research list` -- the CLI over `api.viewers.view_list`.

Rendering only: which rows, in what order and under which filters is the engine's
(`services/viewers.py`). This module adds the two things that are CLI policy -- the
`bug list` default of open bugs, and the done/total column of `phase list` -- and keeps
the parser wiring in one place so `cli.py` needs a single `register` call.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ...api import view_list
from ...services import viewers as V
from ..context import NOTHING, OK, Ctx

_HELP = {
    "state": "only rows in this state",
    "phase": "only rows under this phase id",
    "tag": "only rows carrying this tag",
    "agent": "only rows owned (leased) by this agent",
    "since": "only rows last changed at or after this ISO date or timestamp",
}
_LISTS = {
    "task": "tasks (and sub-tasks), newest change first",
    "phase": "phases with done/total progress",
    "bug": "bugs: the open ones unless --all or --state",
    "research": "research notes with their verdicts",
}
#: `research` has no subcommands (`research add` is the verb form), so its list lives
#: behind `research list` as the optional verb.
_SHARED_FLAGS = ("state", "phase", "tag", "agent", "since")


def _add_filters(p: argparse.ArgumentParser, kind: str) -> None:
    for flag in _SHARED_FLAGS:
        if flag in V._FILTERS[kind]:
            p.add_argument(f"--{flag}", default="", help=_HELP[flag])
    p.add_argument(
        "--limit", type=int, default=V.DEFAULT_LIMIT, help="most rows to show (default %(default)s)"
    )
    if kind == "bug":
        p.add_argument("--all", action="store_true", help="include fixed and invalid bugs")


def _progress(st, phase_id: str) -> tuple[int, int]:
    below = [
        st.items[i]
        for i in st.descendants(phase_id)
        if i in st.items and st.items[i].kind == "task" and not st.items[i].removed
    ]
    return sum(1 for t in below if t.state == "done"), len(below)


def _line(kind: str, r: dict[str, Any]) -> str:
    parts = [f"  {r['id']:<18} {r['state']:<10}"]
    if kind == "phase":
        parts.append(f" {r['done']}/{r['total']:<4}")
    parts.append(f" {r['title']}")
    tail = [x for x in (r["owner"] and f"@{r['owner']}", r["updated"][:10]) if x]
    if tail:
        parts.append("  [" + " ".join(tail) + "]")
    return "".join(parts)


def cmd_list(a, c: Ctx) -> int:
    kind = a.list_kind
    state = getattr(a, "state", "") or ""
    defaulted = kind == "bug" and not state and not getattr(a, "all", False)
    if defaulted:
        state = "open"
    out = view_list(
        c.repo,
        kind,
        state=state,
        phase=getattr(a, "phase", "") or "",
        tag=getattr(a, "tag", "") or "",
        agent=getattr(a, "agent", "") or "",
        since=getattr(a, "since", "") or "",
        limit=a.limit,
    )
    if "rows" in out.data and kind == "phase":
        st = c.store.ensure(c.log)
        for r in out.data["rows"]:
            r["done"], r["total"] = _progress(st, r["id"])
    if c.json:
        print(json.dumps(out.data, indent=2, default=str))
        if out.exit not in (OK, NOTHING):
            print(out.reason, file=sys.stderr)
        return out.exit
    if out.exit not in (OK,):
        print(out.reason)
        return out.exit
    for r in out.data["rows"]:
        print(_line(kind, r))
    if out.data["truncated"]:
        print(f"\n(showing {out.data['shown']} of {out.data['total']}; raise --limit)")
    if defaulted:
        print("\n(open bugs only; --all includes fixed and invalid)")
    return OK


def register(s) -> None:
    """Add `list` to the task, phase, bug and research parsers already on `s`."""
    for kind in ("task", "phase", "bug"):
        group = s.choices[kind]
        sub = next(x for x in group._actions if isinstance(x, argparse._SubParsersAction))
        lp = sub.add_parser("list", help=f"list {_LISTS[kind]}")
        _add_filters(lp, kind)
        lp.set_defaults(fn=cmd_list, list_kind=kind)
    rs = s.choices["research"]
    verb = next(x for x in rs._actions if x.dest == "verb")
    verb.choices = ["add", "list"]
    verb.help = "optional: `research add` = `research`; `research list` lists the notes"
    _add_filters(rs, "research")
    # `list` needs neither field, so argparse cannot require them for the verb-less form.
    required = [x for x in rs._actions if x.required and x.option_strings]
    for x in required:
        x.required = False
        x.default = None  # so "not given" is None, and an empty value still counts as given
    add_fn = rs.get_default("fn")

    def research(a, c: Ctx) -> int:
        if a.verb == "list":
            a.list_kind = "research"
            return cmd_list(a, c)
        for x in required:
            if getattr(a, x.dest) is None:
                rs.error(f"the following arguments are required: {x.option_strings[0]}")
        return add_fn(a, c)

    rs.set_defaults(fn=research)
