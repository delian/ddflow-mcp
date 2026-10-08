"""`ddflow task|phase|bug|research list` -- the CLI over `api.viewers.view_list`.

Rendering only: which rows, in what order and under which filters is the engine's
(`services/viewers.py`). This module adds the two things that are CLI policy -- the
`bug list` default of open bugs, and the done/total column of `phase list` -- and keeps
the parser wiring in one place so `cli.py` needs a single `register` call.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ...api import phase_progress, view_list
from ...services import viewers as V
from ..context import NOTHING, OK, Ctx
from ..render import emit_json

_HELP = {
    "state": "only rows in this state",
    "phase": "only rows under this phase id",
    "tag": "only rows carrying this tag",
    "owner": "only rows leased by this agent (not --agent, which is who YOU are)",
    "since": "only rows last changed at or after this ISO date or timestamp",
}
_LISTS = {
    "task": "tasks (and sub-tasks), newest change first",
    "phase": "phases with done/total progress",
    "bug": "bugs: the open ones unless --all or --state",
    "research": "research notes with their verdicts",
    "lesson": "lessons, newest first: the live ones unless --all or --state",
}
#: `research` has no subcommands (`research add` is the verb form), so its list lives
#: behind `research list` as the optional verb.
_SHARED_FLAGS = ("state", "phase", "tag", "owner", "since")


def _add_filters(p: argparse.ArgumentParser, kind: str) -> None:
    # `None` defaults, so `research add --state x` (a list flag on the add form) is
    # detectable and refused instead of silently dropped.
    for flag in _SHARED_FLAGS:
        # The engine calls the leaseholder filter `agent`; the CLI cannot, because the
        # global `--agent` (identity) is mirrored onto every subparser under that name.
        if ("agent" if flag == "owner" else flag) in V._FILTERS[kind]:
            p.add_argument(f"--{flag}", default=None, help=_HELP[flag])
    p.add_argument(
        "--limit", type=int, default=None, help=f"most rows to show (default {V.DEFAULT_LIMIT})"
    )
    if kind == "bug":
        p.add_argument("--all", action="store_true", help="include fixed and invalid bugs")
        p.add_argument(
            "--item", default=None, help="only bugs filed in or against this queue item id"
        )
    if kind == "lesson":
        p.add_argument("--all", action="store_true", help="include superseded lessons")


def _line(kind: str, r: dict[str, Any]) -> str:
    parts = [f"  {r['id']:<18} {r['state']:<10}"]
    if kind == "phase":
        parts.append(f" {r['done']}/{r['total']:<4}")
    parts.append(f" {r['title']}")
    tail = [x for x in (r["owner"] and f"@{r['owner']}", r["updated"][:10]) if x]
    if tail:
        parts.append("  [" + " ".join(tail) + "]")
    if kind == "bug":
        # The two facts worth a column: the lesson the close recorded, and how many
        # regression tests guard it. The names themselves are one `ddflow show <id>` away.
        marks = []
        if r.get("lesson"):
            marks.append(f"lesson {r['lesson']}")
        tests = r.get("regression_tests") or []
        if tests:
            marks.append(f"{len(tests)} test" + ("s" if len(tests) != 1 else ""))
        if marks:
            parts.append("  {" + "; ".join(marks) + "}")
    return "".join(parts)


#: What "not --all" narrows to, per kind: the state that means "still live". The engine
#: has no opinion here -- it is CLI policy, set once, and named so the two callers (the
#: CLI and `view_read`) cannot drift.
_NARROW_TO = {"bug": "open", "lesson": "live"}


def cmd_list(a, c: Ctx) -> int:
    kind = a.list_kind
    state = getattr(a, "state", "") or ""
    defaulted = kind in _NARROW_TO and not state and not getattr(a, "all", False)
    if defaulted:
        state = _NARROW_TO[kind]
    out = view_list(
        c.repo,
        kind,
        state=state,
        phase=getattr(a, "phase", "") or "",
        tag=getattr(a, "tag", "") or "",
        agent=getattr(a, "owner", "") or "",
        since=getattr(a, "since", "") or "",
        item=getattr(a, "item", "") or "",
        limit=V.DEFAULT_LIMIT if a.limit is None else a.limit,
    )
    if "rows" in out.data and kind == "phase":
        st = c.store.ensure(c.log)
        for r in out.data["rows"]:
            r["done"], r["total"] = phase_progress(st, r["id"])
    if c.json:
        body = {**out.data, "reason": out.reason} if out.exit != OK else dict(out.data)
        # Echo the filter under the flag's own name: replaying `agent` would be identity.
        if "agent" in body.get("filters", {}):
            body["filters"] = {
                ("owner" if k == "agent" else k): v for k, v in body["filters"].items()
            }
        emit_json(body)
        if out.exit not in (OK, NOTHING):
            print(out.reason, file=sys.stderr)
        return out.exit
    if out.exit != OK:
        print(out.reason, file=sys.stdout if out.exit == NOTHING else sys.stderr)
        return out.exit
    for r in out.data["rows"]:
        print(_line(kind, r))
    if out.data["truncated"]:
        print(f"\n(showing {out.data['shown']} of {out.data['total']}; raise --limit)")
    if defaulted:
        what = "fixed and invalid" if kind == "bug" else "superseded"
        print(f"\n({_NARROW_TO[kind]} {kind}s only; --all includes {what})")
    return OK


def register(s) -> None:
    """Add `list` to the task, phase, bug, lesson and research parsers already on `s`, and
    the sibling `search` viewer (registered here so `cli.py`, a hot file, needs no change)."""
    from .viewers_search import register as register_search

    register_search(s)
    for kind in ("task", "phase", "bug", "lesson"):
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
        stray = [f"--{f}" for f in (*_SHARED_FLAGS, "limit") if getattr(a, f, None) is not None]
        if stray:
            rs.error(f"{', '.join(stray)} apply to `research list`, not to recording a finding")
        for x in required:
            if getattr(a, x.dest) is None:
                rs.error(f"the following arguments are required: {x.option_strings[0]}")
        return add_fn(a, c)

    rs.set_defaults(fn=research)
