"""The `[bugs]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class BugsConfig:
    """What recording a bug does to the queue, `[bugs]`."""

    #: `bug found` files the fix task (`fix-<bug>`) in the queue, so the bug is an item in
    #: the DAG: offered first (`[schedule] bugs_first`), holding its files against features,
    #: closed by the task's completion with a regression test. `bug found --no-task` for a
    #: bug fixed in the commit that found it.
    file_task: bool = True
    #: The standing phase a fix task goes under when the bug names no item, or an item whose
    #: phase is finished. Created on first use.
    phase: str = "bugs"


_doc(
    "bugs",
    "file_task",
    "Whether `bug found` files the bug's fix task in the queue (default true): `fix-<bug id>`, tagged as a bug fix, under the phase of the item the bug names (or `[bugs] phase`), carrying that item's globs unless `--globs` says otherwise, and linked to the bug. `bugs_first` offers it before features and a feature on the same files waits behind it, so bugs do not pile up under new work. `complete <fix task>` refuses while the bug is open and `--regression-test` closes it; `bug found --no-task` files no task (a bug fixed in the same commit); `bug file-tasks` files one for every open bug that has none. When the item named is itself an OPEN bug-fix task, that task is the fix and nothing new is filed. `false`: bugs are flat records, as before.",
)
_doc(
    "bugs",
    "phase",
    "The standing phase (default `bugs`) a fix task is filed under when the bug names no item, or names one whose phase is already finished. Made on first use, with the title `Bugs`.",
)
