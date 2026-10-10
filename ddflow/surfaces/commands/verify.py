"""`ddflow verify` -- the human surface for `api.verify` / `api.verify_sweep`.

One item (`verify <id>`) or a sweep over every done task (`--all`, or `--phase P`). Same
calls and wire bodies as the `ddflow_verify` MCP tool; exit 1 when a claim does not hold,
2 when there is nothing to verify, 0 otherwise.
"""

from __future__ import annotations

import sys

from ...api.verify import judge, pack, verify, verify_sweep
from ..context import FAIL, NOTHING, REFUSED, Ctx
from ..render import emit_json

_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "unknown": "??  "}


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
        emit_json(out.body(keys))
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
        emit_json(out.body(""))
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


def _pack(a, c: Ctx) -> int:
    out = pack(c.repo, a.id)
    if out.exit != 0:
        print(out.reason, file=sys.stderr)
        return out.exit
    if c.json:
        emit_json(out.body(("id", "pack")))
    else:
        print(out.data["pack"])
    return out.exit


def _judge(a, c: Ctx) -> int:
    out = judge(c.repo, a.id, on_progress=None if c.json else print)
    if c.json:
        emit_json(out.body(""))
    elif out.exit != 0 or not out.data.get("text"):
        print(out.reason or out.data.get("text", ""), file=sys.stderr if out.exit else sys.stdout)
    return out.exit


_PACK_ALONE = "--pack or --judge (one of them) takes one task id and nothing else"
_ONE_NOT_SWEEP = "--all, --phase, --limit and --file-bugs are for a sweep, not a single task"


def _pack_problem(a) -> bool:
    """--pack / --judge take an id and nothing else, and not both."""
    extras = (a.all, a.phase, a.file_bugs, a.reopen, a.reason, a.force, a.limit is not None)
    return not a.id or any(extras) or bool(a.pack and a.judge)


def _refusal(a) -> str:
    """What is wrong with the combination of arguments, or "" when it is a mode of its own."""
    if a.pack or a.judge:
        return _PACK_ALONE if _pack_problem(a) else ""
    if a.id:
        sweep = (a.file_bugs, a.all, a.phase, a.limit is not None)
        return _ONE_NOT_SWEEP if any(sweep) else ""
    if a.reopen or a.reason or a.force:
        return "--reopen, --reason and --force need a task id"
    if a.all or a.phase:
        return ""
    return "verify what? give a task id, or --all / --phase P for a sweep"


def cmd_verify(a, c: Ctx) -> int:
    if problem := _refusal(a):
        print(problem, file=sys.stderr)
        return FAIL
    if a.pack or a.judge:
        return _pack(a, c) if a.pack else _judge(a, c)
    return _one(a, c) if a.id else _sweep(a, c)
