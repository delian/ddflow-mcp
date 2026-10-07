"""`ddflow` subcommands: pull requests, versions, promotion and the branching flow.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.flow import cmd_flow, cmd_pr, cmd_promote, cmd_version


def register(s: argparse._SubParsersAction) -> None:
    """Add the flow subcommands to `s`, the root `ddflow` subparsers."""
    pr = s.add_parser(
        "pr", help="pull/merge requests: what reviewers did ([flow].integration = pr)"
    )
    pr_s = pr.add_subparsers(dest="pr_cmd", required=True)
    psy = pr_s.add_parser(
        "sync",
        help="ask the forge about every request in review: complete merged ones, reopen "
        "ones with requested changes, merge approved ones",
    )
    psy.add_argument("--item", default="", help="only this item")
    psy.set_defaults(fn=cmd_pr)
    pst = pr_s.add_parser("status", help="every item's request, from the log (no forge call)")
    pst.set_defaults(fn=cmd_pr)
    pth = pr_s.add_parser(
        "threads",
        help="an item's review threads from the forge; with --thread, reply and/or resolve one",
    )
    pth.add_argument("id")
    pth.add_argument("--thread", default="", help="the thread's id (as listed)")
    pth.add_argument("--reply", default="", help="post this reply on --thread")
    pth.add_argument("--resolve", action="store_true", help="mark --thread resolved")
    pth.set_defaults(fn=cmd_pr)

    ver = s.add_parser("version", help="version tags: the next version, and cutting it")
    ver_s = ver.add_subparsers(dest="version_cmd", required=True)
    vsh = ver_s.add_parser("show", help="current version, next version, why, release notes")
    vsh.add_argument("--bump", default="", choices=["", "major", "minor", "patch"])
    vsh.add_argument("--line", default="", help="a maintenance line (default: the current one)")
    vsh.set_defaults(fn=cmd_version)
    vln = ver_s.add_parser(
        "lint",
        help="is every knob and event-kind change in this tree announced in the upgrade manifest?",
    )
    vln.add_argument(
        "--waive",
        default="",
        metavar="CHANGE",
        help="let one unmanifested change (<kind>:<key>) ship, recorded with --reason: the "
        "operator's decision",
    )
    vln.add_argument("--reason", default="", help="why --waive")
    vln.set_defaults(fn=cmd_version)
    vct = ver_s.add_parser(
        "cut", help="tag the next version (gitflow: via a release branch, or a release request)"
    )
    vct.add_argument("--bump", default="", choices=["", "major", "minor", "patch"])
    vct.add_argument("--version", dest="set_version", default="", help="exact MAJOR.MINOR.PATCH")
    vct.add_argument(
        "--push", action="store_true", help="publish the tag (and branches) to the remote"
    )
    vct.add_argument("--dry-run", action="store_true")
    vct.add_argument("--line", default="", help="a maintenance line (default: the current one)")
    vct.add_argument(
        "--changelog",
        action="store_true",
        help="also write the version's section into CHANGELOG.md, committed with the cut "
        "(in a release request under gitflow + pr); never written without this flag",
    )
    vct.add_argument(
        "--force", action="store_true", help="with --changelog: replace a hand-edited changelog"
    )
    vct.set_defaults(fn=cmd_version)

    pm = s.add_parser(
        "promote",
        help="environment branches: move work one step downstream ([flow].environments)",
    )
    pm_s = pm.add_subparsers(dest="promote_cmd", required=True)
    pma = pm_s.add_parser("add", help="file a promotion to ENV from the branch just upstream of it")
    pma.add_argument("env")
    pma.add_argument("--force", action="store_true", help="file it even with nothing to carry")
    pma.set_defaults(fn=cmd_promote)
    pmd = pm_s.add_parser(
        "deployed", help="record the sha a deploy put live in ENV (call it from the deploy hook)"
    )
    pmd.add_argument("env")
    pmd.add_argument("--sha", default="", help="the deployed commit (default: ENV's branch head)")
    pmd.set_defaults(fn=cmd_promote)
    pm_s.add_parser(
        "status", help="each environment: head, commits behind upstream, open promotion"
    ).set_defaults(fn=cmd_promote)

    fl = s.add_parser(
        "flow",
        help="how this project works: branching model, release lines, and every workflow "
        "choice with who made it",
    )
    fl_s = fl.add_subparsers(dest="flow_cmd", required=True)
    fl_s.add_parser("show", help="every choice: value, options, and who decided").set_defaults(
        fn=cmd_flow
    )
    fch = fl_s.add_parser(
        "choose", help="record a workflow choice (the config file still wins over it)"
    )
    fch.add_argument("knob")
    fch.add_argument("value")
    fch.add_argument("--reason", default="", help="why — the next agent reads this")
    fch.set_defaults(fn=cmd_flow)
