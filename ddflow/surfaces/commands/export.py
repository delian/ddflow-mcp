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
from ..context import NOTHING, OK, REFUSED, Ctx


def add_export_parser(sub) -> None:
    """Register `export` on the top-level subparsers (called by `cli.build_parser`)."""
    p = sub.add_parser(
        "export",
        help="documents generated from the log: list, print, enable, disable, --diff, --check, --update",
    )
    p.add_argument(
        "doc",
        nargs="?",
        default="",
        help="the document kind; omit to list the kinds. Or a verb: enable <doc>, disable <doc>, "
        "ack, eject <doc>, validate [<doc>]",
    )
    p.add_argument("target", nargs="?", default="", help="the document, after a verb")
    p.add_argument("--path", default="", help="enable: the target file (repo-relative)")
    p.add_argument("--mode", default="", help="enable: whole, region or append")
    p.add_argument("--local", action="store_true", help="enable/disable: this machine only")
    p.add_argument(
        "--lock", action="store_true", help="disable: the operator's veto; agents cannot enable it"
    )
    p.add_argument("--all", action="store_true", help="act on the selected documents")
    for flag, what in (
        ("--since", "entries at or after this date (YYYY-MM-DD or a timestamp prefix)"),
        ("--version", "one release (the changelog kind: X or vX, or unreleased)"),
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


VERBS = ("enable", "disable", "ack", "eject", "validate")


def _list(c: Ctx, *, may_ack: bool = True) -> int:
    out = A.export_list(c.repo, c.requested_agent)
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
        if r.get("locked"):
            state += " (locked by the operator)"
        if r.get("enabled_by"):
            when = str(r.get("enabled_at", ""))[:16].replace("T", " ")
            state += f" -- enabled by {r['enabled_by']} {when}"
            state += (
                " (not acknowledged)" if r.get("by_agent") and not r.get("acknowledged") else ""
            )
        print(f"{r['doc']:<{w}}  {r['target']:<{t}}  {r['mode']:<6}  {state}")
    sel = out.data["selected"]
    print(
        f"\nselected: {', '.join(sel) if sel else '(none)'}. "
        "Print any one with `ddflow export <doc>`; `--all` acts on the selected set."
    )
    if sel and not out.data["redaction_applied"]:
        print("redaction: [export].redact is not applied yet (B-export-redact-fence)")
    pending = out.data.get("unacknowledged") or []
    if pending:
        print(
            "enabled by an agent: "
            + ", ".join(f"{r['doc']} (by {r['by'] or '?'})" for r in pending)
            + ". Stop one with `ddflow export disable <doc>`; add --lock to veto it."
        )
        if may_ack and sys.stdin.isatty() and sys.stdout.isatty() and not c.requested_agent:
            # A person looking at the list is the acknowledgement (an agent's marker refuses).
            ack = A.export_ack(c.repo, agent=c.requested_agent)
            if ack.exit == OK and ack.data.get("documents"):
                print("(acknowledged)")
    return OK


def _plain(out, c: Ctx) -> int:
    """One-line result of enable / disable / ack / eject."""
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    if out.exit:
        print(out.reason, file=sys.stderr)
        return out.exit
    print(out.data["text"])
    for w in out.data.get("warnings", []) + out.data.get("notes", []):
        print(f"note: {w}", file=sys.stderr)
    return OK


def _validate(a, c: Ctx) -> int:
    out = A.export_validate(c.repo, a.target)
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    for r in out.data.get("results", []):
        if r["ok"]:
            print(f"{r['doc']}: ok ({r['template']})")
        else:
            print(f"{r['doc']}: {r['message']}", file=sys.stderr)
    for n in out.data.get("notes", []):
        print(f"note: {n}")
    if out.exit and not out.data.get("results"):
        print(out.reason, file=sys.stderr)
    return out.exit


def _verb(a, c: Ctx) -> int:
    verb, doc = a.doc, a.target
    if verb == "validate":
        return _validate(a, c)
    if verb == "ack":
        return _plain(A.export_ack(c.repo, agent=c.requested_agent), c)
    if not doc:
        print(f"ddflow export {verb} needs a document: ddflow export {verb} <doc>", file=sys.stderr)
        return REFUSED
    if verb == "enable":
        return _plain(
            A.export_enable(
                c.repo, doc, path=a.path, mode=a.mode, local=a.local, agent=c.requested_agent
            ),
            c,
        )
    if verb == "disable":
        return _plain(
            A.export_disable(c.repo, doc, lock=a.lock, local=a.local, agent=c.requested_agent),
            c,
        )
    return _plain(A.export_eject(c.repo, doc, force=a.force), c)


def cmd_export(a, c: Ctx) -> int:
    if a.doc in VERBS:
        return _verb(a, c)
    if a.target:
        print(f"unexpected argument {a.target!r}", file=sys.stderr)
        return REFUSED
    if not a.doc and not a.all:
        return _list(c, may_ack=not (a.diff or a.check or a.update or a.out))
    interactive = bool(
        a.update and not a.yes and not c.json and sys.stdin.isatty() and sys.stdout.isatty()
    )  # --json is machine output: no prompt
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
