"""`ddflow onboard ...` -- the human surface for `api.onboard`."""

from __future__ import annotations

import json
import sys

from ...api import onboard_run
from ...api.onboard import STAGES
from ..context import Ctx


def add_onboard_parser(sub) -> None:
    ob = sub.add_parser(
        "onboard",
        help="run an onboarding stage: one report, propose by default "
        "(status|preflight|legacy|memory|test-gate|verify)",
    )
    ob.add_argument(
        "stage",
        nargs="?",
        default="status",
        choices=list(STAGES),
        help="which stage (default status: the standing drift report)",
    )
    ob.add_argument(
        "--apply",
        action="store_true",
        help="act on the stage's proposal (preflight/legacy/memory)",
    )
    ob.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="NAME",
        help="approve one named item (repeatable); without it, apply acts on everything "
        "the report marked safe",
    )
    ob.set_defaults(fn=cmd_onboard)


def cmd_onboard(a, c: Ctx) -> int:
    out = onboard_run(
        c.repo,
        stage=a.stage,
        apply=bool(a.apply),
        accept=tuple(a.accept or ()),
        agent=c.requested_agent,
    )
    text = out.data.get("text") or out.reason
    acted = _result_lines(out.data)
    if acted:
        # The report was rendered BEFORE apply ran; a person at a terminal must see
        # what the apply DID, not only the offer it came from (roborev on c61278a4).
        text = f"{text}\n{acted}"
    if c.json:
        print(json.dumps(out.body(""), indent=2, default=str))
    elif out.exit in (1, 3):
        print(text, file=sys.stderr)
    else:
        # Exit 2 is "nothing to do", a REPORT: it belongs on stdout like a success.
        print(text)
    return out.exit


def _result_lines(data: dict) -> str:
    lines: list[str] = []
    for outcome in data.get("removed") or []:
        lines.append(f"removed: {outcome.get('name')}")
    for outcome in data.get("failed") or []:
        lines.append(f"failed: {outcome.get('name')}: {outcome.get('detail')}")
    for outcome in data.get("refused") or []:
        lines.append(f"refused: {outcome.get('name')}: {outcome.get('detail')}")
    for action in data.get("actions") or []:
        lines.append(str(action))
    approved = data.get("approved") or []
    if approved:
        lines.append("recorded: " + ", ".join(approved))
    return "\n".join(lines)
