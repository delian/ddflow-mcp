"""`ddflow schedule list|show|search` -- the human surface for `api.schedule`.

The parser is built here (`add_schedule_parser`) so the CLI module needs one line to
mount it; the authoring verbs and the MCP tool come with the job-authoring work.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ...api import schedule as A
from ...core.outcome import FAIL
from ..context import Ctx

_PAYLOADS = {"list": "rows", "search": "rows", "show": ""}


def add_schedule_parser(sub) -> None:
    sc = sub.add_parser("schedule", help="scheduled jobs: list, show and search their definitions")
    verbs = sc.add_subparsers(dest="schedule_cmd")
    ls = verbs.add_parser("list", help="every job, merged from the log, files and [cadence]")
    ls.add_argument("--tag", default="", help="only jobs carrying this tag")
    ls.add_argument("--enabled", action="store_true", help="only enabled jobs")
    sh = verbs.add_parser("show", help="one job: definition, source, needs, conflicts, runs")
    sh.add_argument("id")
    se = verbs.add_parser("search", help="jobs matching every word of the query")
    se.add_argument("query")
    sc.set_defaults(fn=cmd_schedule)


def _cadence(c: dict[str, Any]) -> str:
    if not c:
        return "-"
    key, n = next(iter(c.items()))
    return (
        f"{key.removeprefix('every_')}={n:g}"
        if isinstance(n, float)
        else f"{key.removeprefix('every_')}={n}"
    )


def _line(r: dict[str, Any]) -> str:
    off = "" if r.get("enabled", True) else "  (disabled)"
    title = f"  {r['title']}" if r.get("title") else ""
    return f"{r['id']:<22} {_cadence(r.get('cadence') or {}):<12} {r.get('mode', ''):<6} [{r['source']}]{title}{off}"


def _show(d: dict[str, Any]) -> str:
    lines = [f"{d['id']}  {d.get('title') or ''}".rstrip(), f"  source    {d['source']}"]
    if d.get("shadows"):
        lines.append(f"  shadows   {', '.join(d['shadows'])}")
    lines += [
        f"  cadence   {_cadence(d.get('cadence') or {})}   mode {d.get('mode')}   missed "
        f"{d.get('missed')}   jitter {d.get('jitter')}m   "
        f"{'enabled' if d.get('enabled') else 'disabled'}",
    ]
    for label, key in (
        ("needs", "needs"),
        ("needed by", "needed_by"),
        ("scope", "scope_globs"),
        ("tags", "tags"),
    ):
        if d.get(key):
            lines.append(f"  {label:<9} {', '.join(d[key])}")
    if d.get("concurrency_group"):
        lines.append(f"  group     {d['concurrency_group']}")
    if d.get("prompt"):
        lines.append(f"  prompt    {d['prompt']}")
    if d.get("budget"):
        lines.append("  budget    " + ", ".join(f"{k}={v}" for k, v in d["budget"].items()))
    for c in d.get("conflicts", []):
        lines.append(f"  not beside {c['id']}: {'; '.join(c['why'])}")
    runs = d.get("runs", [])
    lines.append(f"  runs      {len(runs)}" + (f", last {runs[-1].get('at', '')}" if runs else ""))
    return "\n".join(lines)


def cmd_schedule(a, c: Ctx) -> int:
    verb = a.schedule_cmd or "list"
    if verb == "show":
        out = A.schedule_show(c.repo, a.id)
    elif verb == "search":
        out = A.schedule_search(c.repo, a.query)
    else:
        out = A.schedule_list(c.repo, tag=a.tag, enabled_only=a.enabled)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return out.exit
    if c.json:
        print(json.dumps(out.body(_PAYLOADS[verb]), indent=2, default=str))
        return out.exit
    if verb == "show":
        print(_show(out.data))
    elif out.data.get("rows"):
        print("\n".join(_line(r) for r in out.data["rows"]))
    else:
        print(out.reason)
    for e in out.data.get("errors", []):
        print(f"problem: {e}", file=sys.stderr)
    return out.exit
