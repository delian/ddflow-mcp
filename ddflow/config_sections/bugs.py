"""The `[bugs]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob
from .ids import id_problem

_MIN_BUNDLE = 1024  #: a smaller cap would leave no room for the skeleton


@declare("bugs")
@dataclass
class BugsConfig:
    """What recording a bug does to the queue, `[bugs]`."""

    #: `bug found` files the fix task (`fix-<bug>`) in the queue, so the bug is an item in
    #: the DAG: offered first (`[schedule] bugs_first`), holding its files against features,
    #: closed by the task's completion with a regression test. `bug found --no-task` for a
    #: bug fixed in the commit that found it.
    file_task: bool = knob(
        True,
        doc="Whether `bug found` files the bug's fix task in the queue (default true): `fix-<bug id>`, tagged as a bug fix, under the phase of the item the bug names (or `[bugs] phase`), carrying that item's globs unless `--globs` says otherwise, and linked to the bug. `bugs_first` offers it before features and a feature on the same files waits behind it, so bugs do not pile up under new work. `complete <fix task>` refuses while the bug is open and `--regression-test` closes it; `bug found --no-task` files no task (a bug fixed in the same commit); `bug file-tasks` files one for every open bug that has none. When the item named is itself an OPEN bug-fix task, that task is the fix and nothing new is filed. `false`: bugs are flat records, as before.",
    )
    #: The standing phase a fix task goes under when the bug names no item, or an item whose
    #: phase is finished. Created on first use.
    phase: str = knob(
        "bugs",
        doc="The standing phase (default `bugs`) a fix task is filed under when the bug names no item, or names one whose phase is already finished. Made on first use, with the title `Bugs`.",
        check=id_problem,
    )

    #: What `bug found` collects about the machine and the tree it was found in
    #: (DX-bug-repro-bundle). Stored as an evidence blob, linked from the bug.
    repro_capture: str = knob(
        "full",
        doc="How much of a reproduction bundle `bug found` stores with the bug (default `full`): `off` stores nothing; `basic` the ddflow version, git shas, branch and tree, the agent identity and how it was derived, the DDFLOW_*/CI/gate environment variables (secrets redacted) and the config keys that differ from the defaults with the layer each came from; `full` also the item with its lease and gate records, the machine's load and free disk, the agent's recent events and, with `--test ID`, the pytest output tail. `--repro-capture` overrides it for one bug.",
        choices=("off", "basic", "full"),
        strictest=("off", "stores nothing, so nothing unredacted can be kept"),
    )
    repro_max_bytes: int = knob(
        65536,
        doc="The largest reproduction bundle stored, in bytes (default 65536). Over it, the biggest parts are cut down (output tails first) and the bundle says it was truncated.",
        check=lambda v: (
            "" if isinstance(v, int) and v >= _MIN_BUNDLE else f"must be at least {_MIN_BUNDLE}"
        ),
    )
    repro_tail_bytes: int = knob(
        4096,
        doc="How many trailing bytes of a command's or a test run's output a reproduction bundle keeps (default 4096).",
        check=lambda v: "" if isinstance(v, int) and v >= 0 else "must be a non-negative integer",
    )
    repro_events: int = knob(
        10,
        doc="How many of the filing agent's latest log events a `full` reproduction bundle lists (default 10). ddflow keeps no log of the commands an agent ran, so these stand in for the last commands (0 lists none).",
        check=lambda v: "" if isinstance(v, int) and v >= 0 else "must be a non-negative integer",
    )
    repro_test_timeout_s: int = knob(
        300,
        doc="Seconds `bug found --test ID` lets pytest run to capture its output tail (default 300); a run cut short is recorded as such.",
        check=lambda v: "" if isinstance(v, int) and v >= 1 else "must be a positive integer",
    )
    repro_env: list[str] = knob(
        factory=lambda: [
            "DDFLOW_*",
            "CI",
            "CI_*",
            "GITHUB_*",
            "PYTEST_*",
            "CLAUDE*",
            "*GATE*",
        ],
        doc="Environment variable name patterns (fnmatch) whose values a reproduction bundle records, redacted: the DDFLOW_* variables, CI markers, pytest's and the gate runner's. A variable whose name says it holds a secret (KEY, TOKEN, SECRET, PASSWORD, ...) is recorded as set, never with its value.",
    )
    repro_redact_extra: list[str] = knob(
        factory=list,
        doc="More redaction regexes applied to a reproduction bundle, IN ADDITION to `[session] redact_patterns` and `redact_extra`.",
    )
