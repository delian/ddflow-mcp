"""`ddflow export`: project documents generated from the event log (decision D-export).

Human view of `api.export`: ``ddflow export`` lists the kinds, ``ddflow export <doc>``
prints one (review on demand), and ``--diff`` / ``--check`` / ``--update`` / ``--out`` /
``--all`` compare or write. Exit codes: 0 done or fresh, 1 stale (``--check``), 2 could not
run or nothing selected, 3 refused.
"""

from __future__ import annotations

import sys

from ...api import export as A
from ...core import clock
from ..context import NOTHING, OK, REFUSED, Ctx
from ..render import emit_json


def _confirm(rel: str, diff: str) -> bool:
    sys.stdout.write(diff if diff.endswith("\n") else diff + "\n")
    sys.stdout.write(f"Write {rel}? [y/N] ")
    sys.stdout.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


VERBS = ("enable", "disable", "ack", "eject", "validate")


def _state_text(r: dict) -> str:
    """The STATE column of one document row."""
    state = r["state"] + (f" ({r['detail']})" if r["detail"] else "")
    if r.get("locked"):
        state += " (locked by the operator)"
    if r.get("enabled_by"):
        when = clock.fmt_minute(str(r.get("enabled_at", "")))
        state += f" -- enabled by {r['enabled_by']} {when}"
        state += " (not acknowledged)" if r.get("by_agent") and not r.get("acknowledged") else ""
    return state


def _list(c: Ctx, *, may_ack: bool = True) -> int:
    out = A.export_list(c.repo, c.requested_agent)
    if c.json:
        emit_json(out.body())
        return out.exit
    if out.exit:
        print(out.reason, file=sys.stderr)
        return out.exit
    rows = out.data["documents"]
    w = max(len(r["doc"]) for r in rows)
    t = max(len(r["target"]) for r in rows)
    print(f"{'DOCUMENT':<{w}}  {'TARGET':<{t}}  {'MODE':<6}  STATE")
    for r in rows:
        state = _state_text(r)
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
        emit_json(out.body())
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
        emit_json(out.body())
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


def _show_results(results: list[dict]) -> None:
    """Each document's result: its text on stdout, a refusal on stderr."""
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
        emit_json(out.body())
        return out.exit
    results = out.data.get("results", [])
    if not results:
        print(out.reason, file=sys.stderr)
        return out.exit
    _show_results(results)
    if out.data.get("note") and out.exit == OK:
        print(out.data["note"], file=sys.stderr)
    return out.exit if out.exit != NOTHING or results else NOTHING
