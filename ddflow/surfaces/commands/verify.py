"""`ddflow verify` -- the human surface for `api.verify` / `api.verify_sweep`.

One item (`verify <id>`) or a sweep over every done task (`--all`, or `--phase P`). Same
calls and wire bodies as the `ddflow_verify` MCP tool; exit 1 when a claim does not hold,
2 when there is nothing to verify, 0 otherwise.
"""

from __future__ import annotations

import json
import sys

from ...api.verify import verify, verify_sweep
from ..context import FAIL, NOTHING, REFUSED, Ctx

_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "unknown": "??  "}


def add_verify_parser(sub) -> None:
    vf = sub.add_parser(
        "verify",
        help="re-derive the claims behind a done task, or sweep them all (exit 1 = one fails)",
    )
    vf.add_argument("id", nargs="?", default="", help="one done task; omit with --all/--phase")
    vf.add_argument("--all", action="store_true", help="every done task, worst first")
    vf.add_argument("--phase", default="", help="every done task under this phase")
    vf.add_argument("--limit", type=int, default=None, help="how many of the worst to list (20)")
    vf.add_argument(
        "--file-bugs", action="store_true", help="file a bug for each completion that does not hold"
    )
    vf.add_argument(
        "--reopen", action="store_true", help="send a task whose completion fails back to the queue"
    )
    vf.add_argument("--reason", default="", help="with --reopen: why (default: the failed claims)")
    vf.add_argument(
        "--force", action="store_true", help="with --reopen: even when the completion holds"
    )
    vf.set_defaults(fn=cmd_verify)


def _one(a, c: Ctx) -> int:
    out = verify(c.repo, a.id, reopen=a.reopen, reason=a.reason, force=a.force)
    if out.exit == FAIL and not out.data.get("claims"):
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        keys = (
            "item",
            "verdict",
            "claims",
            *(k for k in ("reopened", "reason_given", "appears_landed") if k in out.data),
        )
        print(json.dumps(out.body(keys), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason, file=sys.stderr)
        return NOTHING
    if out.exit == REFUSED:
        print(out.reason, file=sys.stderr)
        return REFUSED
    if out.data.get("reopened"):
        print(f"{out.data['item']}: reopened -- {out.data['reason_given']}")
        return out.exit
    print(f"{out.data['item']}: {out.data['verdict']}")
    for cl in out.data["claims"]:
        print(f"  [{_MARK[cl['status']]}] {cl['id']:<15} {cl['detail']}")
    return out.exit


def _sweep(a, c: Ctx) -> int:
    out = verify_sweep(
        c.repo, phase=a.phase, limit=a.limit if a.limit is not None else 20, file_bugs=a.file_bugs
    )
    if out.exit == FAIL and "checked" not in out.data:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(""), indent=2, default=str))
        return out.exit
    d = out.data
    print(
        f"checked {d['checked']} completed task(s): "
        + ", ".join(f"{n} {k}" for k, n in d["counts"].items() if n)
    )
    for w in d["worst"]:
        print(f"\n{w['item']}: {w['verdict']} (suspicion {w['score']})")
        for p in w["problems"]:
            print(f"  [{_MARK[p['status']]}] {p['id']:<15} {p['detail']}")
    if d["bugs_filed"]:
        print(f"\nfiled bugs for: {', '.join(d['bugs_filed'])}")
    return out.exit


def cmd_verify(a, c: Ctx) -> int:
    if a.id:
        if a.file_bugs or a.all or a.phase or a.limit is not None:
            print(
                "--all, --phase, --limit and --file-bugs are for a sweep, not a single task",
                file=sys.stderr,
            )
            return FAIL
        return _one(a, c)
    if a.reopen or a.reason or a.force:
        print("--reopen, --reason and --force need a task id", file=sys.stderr)
        return FAIL
    if a.all or a.phase:
        return _sweep(a, c)
    print("verify what? give a task id, or --all / --phase P for a sweep", file=sys.stderr)
    return FAIL
