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
    out = onboard_run(c.repo, stage=a.stage, apply=bool(a.apply), accept=tuple(a.accept or ()))
    if c.json:
        print(json.dumps(out.body(""), indent=2, default=str))
    elif out.exit in (1, 3):
        print(out.data.get("text") or out.reason, file=sys.stderr)
    else:
        # Exit 2 is "nothing to do", a REPORT: it belongs on stdout like a success.
        print(out.data.get("text") or out.reason)
    return out.exit
