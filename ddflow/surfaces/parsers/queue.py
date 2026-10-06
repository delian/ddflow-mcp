"""`ddflow` subcommands: adding and reshaping work: init, phase, task, split, resolve, update.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...api import items as A_ITEMS
from .. import dedupe_flags
from ..commands.queue import cmd_phase_add, cmd_resolve, cmd_split, cmd_task_add
from ..commands.setup import cmd_init
from ._common import GLOBS_HELP, _Globs


def register(s: argparse._SubParsersAction) -> None:
    """Add the queue subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_item_update

    s.add_parser("init", help="create .ddflow/ in this repository").set_defaults(fn=cmd_init)

    # A phase and a task differ by two arguments. Writing both blocks out by hand is
    # how `--priority` ended up on one and not the other twice before, and how the
    # `record`/`skip` pair right below this was loop-generated for the same reason.
    def _item_parser(sub, name: str, help_text: str, fn):
        grp = s.add_parser(name, help=help_text)
        sub_p = grp.add_subparsers(dest=f"{name}_cmd", required=True)
        add = sub_p.add_parser("add")
        add.add_argument("id")
        add.add_argument("--title", default="")
        add.add_argument("--needs")
        add.add_argument("--globs", action=_Globs, help=GLOBS_HELP)
        add.add_argument("--tags")
        add.add_argument("--body")
        add.add_argument("--priority", type=int, default=A_ITEMS.DEFAULT_PRIORITY)
        add.add_argument("--line", default="", help="release line (default: the current one)")
        add.add_argument(
            "--readd",
            action="store_true",
            help="file a REMOVED item's id again. An id still in the queue is always "
            "refused: change it with `ddflow update`",
        )
        dedupe_flags.add_flags(add)
        add.set_defaults(fn=fn)
        return add

    _item_parser(s, "phase", "add a phase", cmd_phase_add)
    tad = _item_parser(s, "task", "add a task", cmd_task_add)
    tad.add_argument("--phase", default="", help="owning phase")
    tad.add_argument(
        "--parent",
        default="",
        help="owning phase OR task — a task parent makes this a SUB-TASK, which "
        "carries its own globs and dependencies like any other task",
    )
    tad.add_argument(
        "--lines",
        default="",
        help="a FIX for several release lines, e.g. 1,2,3: written where [flow].port_strategy "
        "says, with a port task <id>@<line> generated for each other line",
    )
    tad.add_argument(
        "--port-of",
        default="",
        help="a FOLLOW-UP to an earlier fix: takes the lines that fix reached, so its own "
        "ports carry what this one lands",
    )

    sp = s.add_parser(
        "split", help="split an item into sub-tasks in place, keeping its id and history"
    )
    sp.add_argument("id")
    sp.add_argument(
        "--into", action="append", default=[], help="repeatable: 'sub-id=title', or just 'sub-id'"
    )
    sp.add_argument(
        "--globs",
        action=_Globs,
        default="",
        help="globs for the children (default: inherit the parent's); " + GLOBS_HELP,
    )
    sp.add_argument("--needs", default="", help="dependencies for the FIRST child")
    sp.set_defaults(fn=cmd_split)

    rs = s.add_parser(
        "resolve",
        help="settle a CONTESTED item (rival adds or claims from two clones), on the record",
    )
    rs.add_argument("id")
    rs.add_argument(
        "--keep",
        required=True,
        help="the definition's event id (or a 6+ character prefix), or the agent / lease "
        "holder, to keep — `ddflow show <id>` lists them",
    )
    rs.add_argument(
        "--refile-as",
        default="",
        help="re-add each definition NOT kept under these new ids (comma-separated, in "
        "`show` order) in the same transaction",
    )
    rs.set_defaults(fn=cmd_resolve)

    up = s.add_parser("update", help="change an item's fields")
    up.add_argument("id")
    for f in ("title", "body", "needs", "tags", "line", "resources"):
        up.add_argument(f"--{f}")
    up.add_argument(
        "--globs",
        action=_Globs,
        help="REPLACES the item's globs (and a claimed item's lease) with these; " + GLOBS_HELP,
    )
    up.add_argument("--priority", type=int)
    up.add_argument(
        "--worktree",
        help="rebind the item (and your live lease on it) to this linked worktree and the "
        "branch checked out there -- the way out of a binding to the wrong tree",
    )
    up.set_defaults(fn=cmd_item_update)
