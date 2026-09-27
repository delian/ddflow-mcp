"""`pr` and `version` — the human surface for `api.flow` (RESEARCH R16)."""

from __future__ import annotations

import json
import sys

from ...api import flow as A
from ..context import OK, Ctx


def _emit_json(out) -> int:
    print(json.dumps(out.body(), indent=2, default=str))
    if out.exit != OK and out.reason:
        # The body is the contract; WHY it was refused goes where every other command
        # puts it, so a caller reading JSON is not left with an exit code alone.
        print(out.reason, file=sys.stderr)
    return out.exit


def cmd_pr(a, c: Ctx) -> int:
    if a.pr_cmd == "status":
        out = A.pr_status(c.repo, agent=c.requested_agent)
        if c.json:
            return _emit_json(out)
        if not out.data["rows"] and not out.data["releases"]:
            print("no pull requests recorded")
            return OK
        for r in out.data["rows"]:
            print(
                f"  {r['id']:<14} {r['state']:<8} #{r['number']} {r['pr_state']:<7} "
                f"review={r['review'] or '-'} checks={r['checks'] or '-'} "
                f"-> {r['base']}  (as of {r['synced_at'] or 'never'})"
            )
        for r in out.data["releases"]:
            print(
                f"  release {r['version']}: {r.get('url', '')} awaiting merge into {r.get('base', '')}"
            )
        return OK
    out = A.pr_sync(c.repo, item=a.item or "", agent=c.requested_agent)
    if c.json:
        return _emit_json(out)
    for ch in out.data["changes"]:
        print(f"  {ch['item']}: {ch['what']} {ch['detail']} {ch['url']}".rstrip())
    for w in out.data["waiting"]:
        print(
            f"  {w['id']}: waiting (review={w['review'] or '-'}, checks={w['checks'] or '-'}) {w['url']}"
        )
    for u in out.data["unavailable"]:
        print(f"UNAVAILABLE: {u}", file=sys.stderr)
    for r in out.data["refused"]:
        print(f"REFUSED: {r}", file=sys.stderr)
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
    return out.exit


def cmd_version(a, c: Ctx) -> int:
    if a.version_cmd == "show":
        out = A.version_show(c.repo, bump=a.bump or "", agent=c.requested_agent)
        if c.json:
            return _emit_json(out)
        d = out.data
        print(f"on {d['ref']}: current {d['current'] or '(none)'} ({d['current_tag'] or 'no tag'})")
        if out.exit != OK:
            print(out.reason)
            return out.exit
        print(
            f"next: {d['next']}  ({d['bump']}, {d['commits']} commit(s), {len(d['items'])} item(s))"
        )
        for r in d["reasons"][:12]:
            print(f"  {r}")
        print()
        print(d["notes"])
        return OK
    out = A.version_cut(
        c.repo,
        bump=a.bump or "",
        version=a.set_version or "",
        push=a.push,
        dry_run=a.dry_run,
        agent=c.requested_agent,
    )
    if c.json:
        return _emit_json(out)
    for step in out.data["steps"]:
        print(f"  {step}")
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    if out.data["warning"]:
        print(f"WARNING: {out.data['warning']}", file=sys.stderr)
    print(f"{out.data['tag']}{' (dry run)' if out.data['dry_run'] else ''}")
    return OK
