"""`ddflow export`: project documents generated from the event log (decision D-export).

Human view of `api.export`: ``ddflow export`` lists the kinds, ``ddflow export <doc>``
prints one (review on demand), and ``--diff`` / ``--check`` / ``--update`` / ``--out`` /
``--all`` compare or write. Exit codes: 0 done or fresh, 1 stale (``--check``), 2 could not
run or nothing selected, 3 refused.
"""

from __future__ import annotations

import json
import sys

from ...api import export as A
from ..context import NOTHING, OK, Ctx


def add_export_parser(sub) -> None:
    """Register `export` on the top-level subparsers (called by `cli.build_parser`)."""
    p = sub.add_parser(
        "export",
        help="documents generated from the log: list, print, --diff, --check, --update, --all",
    )
    p.add_argument("doc", nargs="?", default="", help="the document kind; omit to list the kinds")
    p.add_argument("--all", action="store_true", help="act on the selected documents")
    for flag, what in (
        ("--since", "entries at or after this date (YYYY-MM-DD or a timestamp prefix)"),
        ("--version", "entries of one release"),
        ("--phase", "one phase"),
        ("--item", "one item (the bugs document filters it as a phase)"),
        ("--status", "one status, e.g. open or fixed"),
        ("--tag", "one tag"),
        ("--session", "one session id"),
    ):
        p.add_argument(flag, default="", help=what)
    p.add_argument("--limit", type=int, default=0, help="at most N entries")
    p.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="size cap for printing (default [export].max_bytes)",
    )
    p.add_argument(
        "--template", default="", help="render once with this template file; writes nothing"
    )
    p.add_argument("--diff", action="store_true", help="show what a write would change")
    p.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the target is not what a write would produce",
    )
    p.add_argument(
        "--update", action="store_true", help="write the configured target (asks on a terminal)"
    )
    p.add_argument("--out", default="", help="write to this repo-relative path instead")
    p.add_argument(
        "--force", action="store_true", help="overwrite a hand-edited or unmarked target"
    )
    p.add_argument("--yes", action="store_true", help="with --update: do not ask on a terminal")
    p.set_defaults(fn=cmd_export)


def _confirm(rel: str, diff: str) -> bool:
    sys.stdout.write(diff if diff.endswith("\n") else diff + "\n")
    sys.stdout.write(f"Write {rel}? [y/N] ")
    sys.stdout.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _list(c: Ctx) -> int:
    out = A.export_list(c.repo)
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    if out.exit:
        print(out.reason, file=sys.stderr)
        return out.exit
    rows = out.data["documents"]
    w = max(len(r["doc"]) for r in rows)
    t = max(len(r["target"]) for r in rows)
    print(f"{'DOCUMENT':<{w}}  {'TARGET':<{t}}  {'MODE':<6}  STATE")
    for r in rows:
        state = r["state"] + (f" ({r['detail']})" if r["detail"] else "")
        print(f"{r['doc']:<{w}}  {r['target']:<{t}}  {r['mode']:<6}  {state}")
    sel = out.data["selected"]
    print(
        f"\nselected: {', '.join(sel) if sel else '(none)'}. "
        "Print any one with `ddflow export <doc>`; `--all` acts on the selected set."
    )
    if sel and not out.data["redaction_applied"]:
        print("redaction: [export].redact is not applied yet (B-export-redact-fence)")
    return OK


def cmd_export(a, c: Ctx) -> int:
    if not a.doc and not a.all:
        return _list(c)
    interactive = bool(a.update and not a.yes and sys.stdin.isatty() and sys.stdout.isatty())
    out = A.export(
        c.repo,
        a.doc,
        all_docs=a.all,
        since=a.since,
        version=a.version,
        phase=a.phase,
        item=a.item,
        status=a.status,
        limit=a.limit,
        tag=a.tag,
        session=a.session,
        max_bytes=a.max_bytes,
        template=a.template,
        diff=a.diff,
        check=a.check,
        update=a.update,
        out=a.out,
        force=a.force,
        confirm=_confirm if interactive else None,
    )
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    results = out.data.get("results", [])
    if not results:
        print(out.reason, file=sys.stderr)
        return out.exit
    for i, r in enumerate(results):
        if r["action"] == "print":
            if i:
                print()
            sys.stdout.write(r["text"])
        elif r["action"] == "diff":
            sys.stdout.write(r["text"] or f"{r['path']}: no changes\n")
        elif r["action"] in ("refused", "failed"):
            print(f"{r['doc']}: {r['message']}", file=sys.stderr)
            if r["text"]:
                sys.stderr.write(r["text"])
        elif r["action"] == "stale":
            print(f"{r['path']}: stale ({r['doc']})")
            sys.stdout.write(r["text"])
        else:
            print(f"{r['doc']}: {r['message']}")
    if out.data.get("note") and out.exit == OK:
        print(out.data["note"], file=sys.stderr)
    return out.exit if out.exit != NOTHING or results else NOTHING
