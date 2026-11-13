"""The top-level drive: run the rehearsal, and write a failed run's diagnostics."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ...core import clock
from ...infra import fsio
from ._model import DIAG_ENV, FAIL, LIVE_CHANGED, Options, Report
from ._rehearsal import Rehearsal


def _rehearse(opt: Options) -> tuple[Report, Rehearsal]:
    """One rehearsal in ``opt.work``, which is removed afterwards unless it is not ours (a
    scratch directory that already held something is refused, and never cleaned) or ``keep``."""
    work = Path(opt.work)
    ours = not work.exists() or (work.is_dir() and not any(work.iterdir()))
    rehearsal = Rehearsal(opt)
    try:
        return rehearsal.run(), rehearsal
    finally:
        if ours and not opt.keep:
            fsio.rmtree_owned(work)


def diag_directory(opt: Options) -> Path:
    if opt.diag_dir:
        return Path(opt.diag_dir)
    if os.environ.get(DIAG_ENV):
        return Path(os.environ[DIAG_ENV])
    return Path(opt.source) / ".ddflow" / "local" / "dryrun"


def checks(report: Report) -> list[dict[str, Any]]:
    return [asdict(c) for c in report.checks]


def write_diagnostics(opt: Options, report: Report, runs: list[Rehearsal]) -> str:
    """Write the step table of a failed run and each command's bounded output to a file; the
    path ("" when it could not be written: the report must still come out). A rehearsal that was
    repeated keeps every run: its table, its checks and its commands (each tagged with its run)."""
    data = {
        "run": runs[-1].run_id,
        "started": report.started,
        "source": report.source,
        "from_commit": report.from_commit,
        "exit": report.exit_code,
        "table": report.table(),
        "checks": checks(report),
        "facts": report.facts,
        "runs": [
            {"run": r.run_id, "table": r.report.table(), "checks": checks(r.report)} for r in runs
        ],
        "commands": [c for r in runs for c in r.commands],
    }
    name = f"{clock.compact_at()}-{runs[-1].run_id}.json"
    for choice in (diag_directory(opt), None):
        try:
            where = choice or fsio.unused_dir("ddflow-dryrun-diag-")  # the system temp directory
            where.mkdir(parents=True, exist_ok=True)
            fsio.replace_text(where / name, json.dumps(data, indent=1, default=str) + "\n")
            return str(where / name)
        except OSError:
            continue
    return ""


def run_all(opt: Options) -> Report:
    """Run the whole rehearsal and clean up (unless ``opt.keep``). If the live project changed
    under it in a way it cannot tell from its own doing (`LIVE_CHANGED`), it is run once more
    in a scratch directory of its own; the second run decides, and a project that moved again
    leaves that check `unavailable`. A failed run, one that could not run (a clone or a build that
    failed; not an old ddflow that was never offered) or one repeated because the project moved
    leaves its step table and every command's output in a file, named in the report."""
    original = opt.old  # building the old ddflow replaces it, in a scratch directory that goes
    report, rehearsal = _rehearse(opt)
    runs = [rehearsal]
    if report.facts.get("live_changed"):
        first = report.facts["live_changed"]
        again = replace(opt, work=Path(f"{opt.work}-retry"), old=original)
        report, rehearsal = _rehearse(again)
        runs.append(rehearsal)
        report.facts["first_run"] = f"{LIVE_CHANGED}: {'; '.join(first)}"
    unexplained = (
        report.facts.get("live_changed")
        or report.facts.get("first_run")
        or report.facts.get("could_not_run")
    )
    if any(c.status == FAIL for c in report.checks) or unexplained:
        report.diagnostics = write_diagnostics(opt, report, runs)
    return report
