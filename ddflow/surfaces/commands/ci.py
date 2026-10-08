"""`ddflow ci ...` -- the human surface for `api.ci`."""

from __future__ import annotations

import sys
from pathlib import Path

from ...api import ci as A
from ...infra import worktree as W
from ..context import Ctx
from ..render import emit_json


def add_ci_parser(sub) -> None:
    ci = sub.add_parser(
        "ci", help="the CI parity gate: run the pre-push checks on the merge result"
    )
    ci.add_argument(
        "verb",
        nargs="?",
        choices=["run", "status", "record"],
        default="status",
        help="run: check the merge result; status (default): what would run, and whether it can",
    )
    ci.add_argument("--ref", default="", help="run: the commit or item id (default: HEAD here)")
    ci.add_argument(
        "--base", default="", help="run: merge this branch in first (default [ci].base)"
    )
    ci.add_argument("--command", default="", help="run: use this instead of [ci].command")
    ci.add_argument(
        "--stage", default="pre-push", help="record: gate | merge | pre-push | schedule"
    )
    ci.add_argument(
        "--result", choices=["passed", "failed"], default="", help="record: how it went"
    )
    ci.add_argument(
        "--report",
        default="",
        help="record: file with the pre-commit output, parsed for the failing checks",
    )
    ci.add_argument("--sha", default="", help="record: the commit that was checked")
    ci.set_defaults(fn=cmd_ci)


def _here_or_head(repo: Path) -> str:
    """HEAD of the current directory when it is a worktree of THIS repository, else HEAD."""
    here = Path.cwd()
    common = [
        W.git(p, "rev-parse", "--path-format=absolute", "--git-common-dir") for p in (here, repo)
    ]
    if all(r.ok for r in common) and Path(common[0].out).resolve() == Path(common[1].out).resolve():
        return W.rev(here, "HEAD") or "HEAD"
    return "HEAD"


def cmd_ci(a, c: Ctx) -> int:
    if a.verb == "record":
        if not a.result:
            print("ci record needs --result passed|failed", file=sys.stderr)
            return 1
        out = A.record(c.repo, stage=a.stage, ok=a.result == "passed", sha=a.sha, report=a.report)
        if c.json:
            emit_json(out.body(""))
        elif out.exit:
            print(out.reason, file=sys.stderr)
        else:
            print(f"ci: recorded {a.stage} {out.data['status']}")
        return out.exit
    if a.verb == "status":
        out = A.status(c.repo)
        if c.json:
            emit_json(out.body(""))
            return out.exit
        d = out.data
        print(f"command:   {d['command'] or '(none)'}")
        print(f"base:      {d['base']}")
        print(f"timeout:   {d['timeout_s']}s")
        print(f"available: {'yes' if d['available'] else 'NO -- ' + d['why']}")
        return out.exit
    # The default is the HEAD of the tree the command runs in: as a gate it runs in the
    # item's worktree, while c.repo is the primary checkout (whose HEAD is main).
    ref = a.ref or _here_or_head(c.repo)
    out = A.run(c.repo, ref=ref, base=a.base, command=a.command)
    if c.json:
        emit_json(out.body(""))
        return out.exit
    d = out.data
    if d.get("command"):
        merged = f" merged with {d['merged_with']}" if d.get("merged_with") else ""
        print(f"ci: {d['command']}  on {d.get('sha', '')[:10]}{merged}")
    for ch in d.get("checks", []):
        print(
            f"  [{'ok  ' if ch['ok'] else 'FAIL'}] {ch['id']}"
            + (f"  {ch['detail']}" if ch["detail"] else "")
        )
    if out.exit == 0:
        print("ci: passed")
        return 0
    print(out.reason, file=sys.stderr)
    if out.exit == 1 and d.get("output_tail"):
        print(d["output_tail"], file=sys.stderr)
    return out.exit
