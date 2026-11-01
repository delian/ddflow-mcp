"""The command line (scripts/ci/upgrade-dry-run is this, started from a checkout)."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

from ...core import version as V
from ...infra import fsio
from ._drive import run_all
from ._model import Cmd, CouldNotRun, Options
from ._read import previous_release, release_commit, simulated_old, source_root

COULD_NOT_RUN = 2


def tools_budget(tree: Path) -> int:
    """The compact tools/list budget the suite ratchets, read from the tree under test; 0 when
    that tree carries no test of it (an installed package)."""
    path = tree / "tests" / "test_mcp_tool_budget.py"
    text = path.read_text("utf-8") if path.is_file() else ""
    found = re.search(r"^TOOLS_LIST_BUDGET\s*=\s*([\d_]+)", text, re.M)
    return int(found.group(1).replace("_", "")) if found else 0


def parser(doc: str = __doc__ or "") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="upgrade-dry-run",
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", default="", help="the project to rehearse (default: this one)")
    which = ap.add_argument_group("the version the installed ddflow was built from (one of)")
    which.add_argument("--from-commit", default="", help="that commit")
    which.add_argument(
        "--installed-version", default="", help="that release: its tag, else its `release X` commit"
    )
    which.add_argument(
        "--previous-release",
        action="store_true",
        help="the newest release below this tree's version",
    )
    old = ap.add_argument_group("the old ddflow (steps 5 and 8)")
    old.add_argument("--old-cmd", default="", help="a ddflow to run as the old one, e.g. a venv's")
    old.add_argument(
        "--build-old", action="store_true", help="install the clone in a venv (uv) as the old one"
    )
    old.add_argument(
        "--simulate-old", default="", metavar="VERSION", help="this code declaring VERSION"
    )
    ap.add_argument(
        "--confirmations-ok",
        action="store_true",
        help="name the items the operator would be asked to confirm in the preview without "
        "failing (the release check: that is this project's own config, not the release's)",
    )
    ap.add_argument(
        "--max-show",
        type=int,
        default=25,
        help="run `show` on this many items in the read pass (0: every item; slow on a big log)",
    )
    ap.add_argument("--work", default="", help="scratch directory (default: a new temporary one)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    ap.add_argument(
        "--diag-dir",
        default="",
        help="where a failed run writes its step table and each command's output (default: "
        "${DDFLOW_DRYRUN_DIAG_DIR}, else .ddflow/local/dryrun/ in the project)",
    )
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--report", default="", help="also write the report as Markdown to this file")
    ap.add_argument("--preview", default="", help="also write the upgrade preview to this file")
    return ap


def choose_commit(args: argparse.Namespace, source: Path) -> str:
    """The commit the installed ddflow was built from, from exactly one way of naming it."""
    named = [bool(args.from_commit), bool(args.installed_version), args.previous_release]
    if sum(named) != 1:
        raise CouldNotRun(
            "name the commit the installed ddflow was built from: --from-commit, "
            "--installed-version or --previous-release (exactly one)"
        )
    if args.from_commit:
        return str(args.from_commit)
    if args.installed_version:
        commit = release_commit(source, args.installed_version)
        if not commit:
            raise CouldNotRun(f"no tag or release commit for ddflow {args.installed_version}")
        return commit
    running = V.running()
    commit = previous_release(source, running)
    if not commit:
        raise CouldNotRun(f"no release tag below {running}")
    return commit


def old_from(args: argparse.Namespace) -> Cmd | None:
    if args.old_cmd:
        return Cmd(shlex.split(args.old_cmd), "", f"installed: {args.old_cmd}")
    return simulated_old(args.simulate_old) if args.simulate_old else None


def main(argv: list[str] | None = None, *, source: Path | None = None, doc: str = "") -> int:
    """The command line: exit 0 every check passed, 1 one failed, 2 it could not run."""
    args = parser(doc or __doc__ or "").parse_args(argv)
    project = Path(args.source or source or Path.cwd()).resolve()
    try:
        commit = choose_commit(args, project)
    except CouldNotRun as exc:
        print(f"upgrade-dry-run: {exc}", file=sys.stderr)
        return COULD_NOT_RUN
    opt = Options(
        source=project,
        work=Path(args.work) if args.work else fsio.unused_dir("ddflow-dry-run-"),
        from_commit=commit,
        old=old_from(args),
        keep=args.keep,
        tools_budget=tools_budget(Path(source_root())),
        build_old=args.build_old,
        asked_fails=not args.confirmations_ok,
        max_show=args.max_show,
        diag_dir=Path(args.diag_dir) if args.diag_dir else None,
    )
    report = run_all(opt)
    print(json.dumps(report.as_data(), indent=2) if args.json else report.table())
    if args.report:
        fsio.replace_text(args.report, report.markdown())
    if args.preview and report.preview:
        fsio.replace_text(args.preview, report.preview)
    return report.exit_code
