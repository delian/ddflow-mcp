"""`ddflow` subcommands: the loop: next, claim, heartbeat, release, wait, approve, gate, complete, abandon, remove, block, unblock, merge.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...api import lifecycle as A_LIFECYCLE
from ...core.model import GATE_OUTCOMES
from ..commands.gates import cmd_gate
from ..commands.lifecycle import (
    cmd_abandon,
    cmd_block,
    cmd_claim,
    cmd_complete,
    cmd_heartbeat,
    cmd_merge,
    cmd_next,
    cmd_release,
    cmd_remove,
    cmd_unblock,
    cmd_wait,
)
from ._common import GLOBS_HELP, _Globs


def _gate_list_parser(g_s) -> None:
    glist = g_s.add_parser(
        "list",
        help="the gates this project defines; --refuted: the gates passed on refutation",
    )
    glist.add_argument(
        "--refuted",
        action="store_true",
        help="list every gate recorded passed ON REFUTATION (D-unify 5), to spot-check",
    )
    glist.add_argument(
        "--since",
        default="",
        help="with --refuted: only passes recorded at or after this ISO date or timestamp",
    )
    glist.set_defaults(fn=cmd_gate)


def register(s: argparse._SubParsersAction) -> None:
    """Add the lifecycle subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_approve

    nx = s.add_parser(
        "next",
        help="what may start now (exit 2 = nothing actionable, exit 1 = unknown --phase)",
    )
    nx.add_argument(
        "--phase", default="", help="limit to this phase; exit 1 if it names no phase or item"
    )
    nx.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
    nx.set_defaults(fn=cmd_next)

    cl = s.add_parser("claim", help="lease an item + create its worktree (exit 3 = refused)")
    cl.add_argument("id")
    cl.add_argument(
        "--globs",
        action=_Globs,
        help="what this claim writes (recorded on the item too); " + GLOBS_HELP,
    )
    cl.add_argument("--note")
    cl.add_argument("--force", action="store_true")
    cl.add_argument("--no-worktree", action="store_true")
    cl.add_argument(
        "--resources",
        default="",
        help="the resources this claim reserves, e.g. 'gpu:2' (recorded on the item too)",
    )
    cl.set_defaults(fn=cmd_claim)

    hb = s.add_parser("heartbeat", help="renew a lease")
    hb.add_argument("id")
    hb.set_defaults(fn=cmd_heartbeat)
    rl = s.add_parser("release", help="give up a lease")
    rl.add_argument("id")
    rl.add_argument("--note")
    rl.set_defaults(fn=cmd_release)

    wt = s.add_parser(
        "wait",
        help="sleep until an item (or anything) can be claimed; exit 2 = deadline, or waiting "
        "cannot help",
    )
    wt.add_argument("--item", default="", help="the item to wait for (default: anything ready)")
    wt.add_argument("--phase", default="", help="with no --item: anything ready in this phase")
    wt.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
    wt.add_argument(
        "--globs",
        action=_Globs,
        help="with --item: the globs you will claim with, so READY means that claim will "
        "succeed; " + GLOBS_HELP,
    )
    # None = unset, so an explicit 0 ("just ask, do not sleep") is not taken as the default.
    wt.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"seconds to wait (default {A_LIFECYCLE.DEFAULT_WAIT_TIMEOUT_S}; 0 asks without "
        f"waiting)",
    )
    wt.add_argument("--poll", type=float, default=None, help="seconds between log checks")
    wt.set_defaults(fn=cmd_wait)

    ap = s.add_parser(
        "approve",
        help="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
    )
    ap.add_argument("id")
    ap.add_argument("gate")
    ap.add_argument("--note", help="what you looked at, for the record")
    ap.add_argument("--reject", action="store_true", help="refuse it; --reason required")
    ap.add_argument("--reason", help="why it was rejected — a 'no' nobody can act on is a stall")
    ap.set_defaults(fn=cmd_approve)

    g = s.add_parser("gate", help="run / record / inspect a gate")
    g_s = g.add_subparsers(dest="gate_cmd", required=True)
    gst = g_s.add_parser("status")
    gst.add_argument("id")
    gst.set_defaults(fn=cmd_gate, gate="")
    _gate_list_parser(g_s)
    grun = g_s.add_parser("run")
    grun.add_argument("id")
    grun.add_argument("gate")
    grun.set_defaults(fn=cmd_gate)
    gvf = g_s.add_parser(
        "verify",
        help="break what this gate guards and require it to notice (exit 1 = it cannot)",
    )
    gvf.add_argument("id")
    gvf.add_argument("gate")
    gvf.set_defaults(fn=cmd_gate)
    for name in ("record", "skip"):
        gr = g_s.add_parser(name)
        gr.add_argument("id")
        gr.add_argument("gate")
        gr.add_argument(
            "--outcome",
            default="passed",
            choices=list(GATE_OUTCOMES),
            help="failed, unavailable, partial and skipped each require --reason",
        )
        gr.add_argument(
            "--reason",
            default="",
            help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, "
            "partial and skipped (--evidence is what you observed, not a substitute)",
        )
        gr.add_argument("--evidence", default="", help="what you observed: output, a summary")
        gr.add_argument("--command", default="")
        gr.add_argument("--exit-code", type=int)
        gr.add_argument(
            "--model",
            default="",
            help="the REVIEWER's model, for family independence (the author's is "
            "`complete --model`); an author-family name on a reviewer gate is refused",
        )
        if name == "record":
            gr.add_argument(
                "--reviewer-model",
                default="",
                help="like --model, stating that this model IS the reviewer, so an "
                "author-family name is recorded rather than refused",
            )
            gr.add_argument(
                "--reviewed-sha",
                default="",
                help="the commit the review tool ran on (roborev review <sha>); refused "
                "when it is not the item's branch head or a commit of its branch",
            )
        gr.add_argument("--output-file", default="")
        gr.set_defaults(fn=cmd_gate)

    cp = s.add_parser("complete", help="finish an item (exit 3 = gates not satisfied)")
    cp.add_argument("id")
    cp.add_argument("--sha", default="")
    cp.add_argument("--model", default="", help="the AUTHOR's model, for independence check")
    cp.add_argument("--force", action="store_true")
    cp.add_argument(
        "--changelog",
        default="",
        help="'Added: text' (Added|Changed|Deprecated|Removed|Fixed|Security), or "
        "skip / internal to keep it out of the changelog; optional",
    )
    cp.add_argument(
        "--regression-test",
        action="append",
        default=[],
        help="for a fix task: the test that now guards the bug(s) it fixes; closes them "
        "(as `bug fixed` would) and completes. Repeat for several.",
    )
    cp.set_defaults(fn=cmd_complete)

    ab = s.add_parser("abandon", help="stop work on an item without completing it")
    ab.add_argument("id")
    ab.add_argument("--reason", required=True)
    ab.add_argument("--force", action="store_true")
    ab.set_defaults(fn=cmd_abandon)

    rm = s.add_parser("remove", help="take an item out of the queue (recorded, not erased)")
    rm.add_argument("id")
    rm.add_argument("--reason", default="")
    rm.add_argument("--force", action="store_true")
    rm.set_defaults(fn=cmd_remove)

    bl = s.add_parser("block")
    bl.add_argument("id")
    bl.add_argument("--reason", required=True)
    bl.add_argument(
        "--reopen",
        action="store_true",
        help="block an item that is already DONE or ABANDONED (refused without)",
    )
    bl.set_defaults(fn=cmd_block)

    ub = s.add_parser(
        "unblock", help="release a blocked item back into the queue (exit 2 = not blocked)"
    )
    ub.add_argument("id")
    ub.add_argument("--note", default="", help="why it is work again")
    ub.set_defaults(fn=cmd_unblock)

    mg = s.add_parser("merge", help="merge an item's branch from the primary checkout")
    mg.add_argument("id")
    mg.add_argument("--message", default="")
    mg.add_argument("--keep", action="store_true")
    mg.add_argument("--allow-dirty", action="store_true")
    mg.add_argument(
        "--allow-empty",
        action="store_true",
        help="land a branch with no commits ahead of its target (refused by default: it "
        "would record the item merged with nothing landed)",
    )
    mg.add_argument(
        "--branch",
        default="",
        help="for an item claimed without a worktree: the branch to land (default: the one "
        "checked out in the worktree you are standing in)",
    )
    mg.add_argument(
        "--model",
        default="",
        help="the AUTHOR's model. In PR mode completion happens later, at `pr sync`, and "
        "the reviewer-independence check needs it then",
    )
    mg.set_defaults(fn=cmd_merge)
