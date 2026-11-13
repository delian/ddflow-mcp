"""`ddflow stacks`: what this repository's stack resolves to in the stack table.

Read-only: the table and the `[verify]` knobs (`services/stacks.py`) answer; nothing is run
(running is the clean-clone pass's job). An answer that is not a resolved stack is exit 2
(`none`: no entry describes the project; `unavailable`: a tool is missing), never 0."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core import outcome as O
from ..services import stacks as S

KIND = "stacks.report"


def report(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    r = S.resolve(Path(repo), Config.load(Path(repo)))
    data = r.as_dict()
    if r.status == "resolved":
        return O.ok(KIND, **data)
    return O.nothing(KIND, r.detail or r.status, **data)


def text(out, a) -> str:
    d = out.data
    lines = [f"stack: {d['stack'] or 'none'}  status: {d['status']}"]
    if d["evidence"]:
        lines.append(f"  read from: {d['evidence']}")
    if d["also"]:
        lines.append(f"  also matches: {', '.join(d['also'])}")
    for role, s in d["steps"].items():
        mark = "" if s["available"] else f"   [unavailable: {', '.join(s['missing'])} not on PATH]"
        lines.append(f"  {role:<10} {s['command']}  ({s['source']}){mark}")
    if d["detail"]:
        lines.append(f"  {d['detail']}")
    if d["next_call"]:
        lines.append(f"  next: {d['next_call']}")
    lines += [f"  warning: {w}" for w in d["warnings"]]
    return "\n".join(lines)
