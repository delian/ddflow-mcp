"""The dry run's data model: the tunables, the messages, the report, and the options."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"

#: What `State` keeps that an upgrade is ALLOWED to change: its own bookkeeping, and the
#: definitions a migration moves into the log. Everything else is the user's records.
UPGRADE_OWNED = frozenset(
    {
        "event_count",
        "last_lamport",
        "ddflow_versions",
        "skew_overrides",
        "upgrades",
        "upgrade_runs",
        "repairs_applied",
        "older_version_events",
        "capabilities",
        "skipped_kinds",
        "fold_problems",
        "defs",
        "links",
        "definitions",
    }
)

#: The line the rehearsal writes by hand inside a stamped block.
HAND_EDIT = "DRYRUN-HAND-EDIT: a line an operator wrote"

#: The event kinds an apply writes: any of them new in the log means something was applied.
APPLYING = frozenset({"upgrade.applied", "repair.applied"})

#: The paths a restore does not bring back: the log only grows, the rest is machine-local.
KEPT = (".ddflow/events/", ".ddflow/local/", ".ddflow/backups/", ".ddflow/worktrees/", ".git/")

#: Commands read-only enough to run on the copy of a project (a derived index may be rebuilt).
READS: tuple[tuple[str, ...], ...] = (
    ("status", "--json"),
    ("board",),
    ("doctor",),
    ("brief",),
    ("history",),
    ("replay",),
)

#: Seconds one ddflow command, git call or server session may take.
TIMEOUT_S = 300.0

#: Where a failed run's step table and command transcript go: `--diag-dir` (`Options.diag_dir`),
#: else this variable, else `.ddflow/local/dryrun/` in the project (git-ignored, machine-local).
DIAG_ENV = "DDFLOW_DRYRUN_DIAG_DIR"
#: The characters kept of a command's stdout or stderr (half from each end); a command that
#: succeeded keeps a tenth of that.
STREAM_BOUND = 6000
#: The most commands a transcript keeps (the last ones; a full rehearsal runs a few hundred).
MAX_COMMANDS = 1000
#: The name of the result when the live project changed in a way the rehearsal cannot tell from
#: its own doing: it is `unavailable`, never a pass and never a fail.
LIVE_CHANGED = "live repo changed during rehearsal"
#: Seconds the rehearsal's MCP sessions are given for the server's start check
#: (`[upgrade].start_timeout_s`, 10 by default): past it the handshake proposes nothing, and the
#: plan of a big log takes 7-11 s under load. The rehearsal tests the flow, not that speed.
START_TIMEOUT_S = 120
#: What the handshake says when the start check ran out of its time bound.
START_TIMED_OUT = "did not finish within"
#: Seconds for the session that must take the timeout path: too little for any plan.
FORCED_TIMEOUT_S = 0.05
#: The pause before the one retry of a clone that failed (a gc or a ref update in the live repo).
CLONE_RETRY_S = 0.5

#: Run as `python -c`: the current code declaring an OLDER release, for tests and for a
#: machine with no old release to hand. It is a simulation, and the report says so.
_SIMULATED_OLD = (
    "import sys, ddflow\n"
    "ddflow.__version__ = sys.argv[1]\n"
    "from ddflow.surfaces.cli import main\n"
    "raise SystemExit(main(sys.argv[2:]))\n"
)


@dataclass
class Check:
    step: int
    name: str
    status: str
    detail: str = ""


@dataclass
class Cmd:
    """A ddflow to run: its argv prefix, the PYTHONPATH it needs ("" for an installed one)
    and how the report names it."""

    argv: list[str]
    pythonpath: str = ""
    label: str = ""


@dataclass
class Options:
    source: Path
    work: Path
    from_commit: str = "HEAD"
    new: Cmd | None = None
    old: Cmd | None = None
    max_show: int = 25
    keep: bool = False
    agents: tuple[str, str] = ("dry-a", "dry-b")
    #: The most bytes a compact `tools/list` may take; 0 reports the size and judges nothing.
    tools_budget: int = 0
    #: Build the old ddflow from the clone (a venv under the scratch directory) when none is given.
    build_old: bool = False
    #: Whether an item the operator would be asked to confirm fails the dry run (the default:
    #: it is the surprise the rehearsal exists to find) or is only named in the preview.
    asked_fails: bool = True
    reason: str = "dry run: the operator would accept this"
    #: Where a failed run writes its transcript; wins over `DDFLOW_DRYRUN_DIAG_DIR`, which wins over
    #: the default `<source>/.ddflow/local/dryrun`.
    diag_dir: Path | None = None


@dataclass
class Report:
    source: str
    from_commit: str
    new_label: str
    old_label: str
    started: str = ""
    checks: list[Check] = field(default_factory=list)
    preview: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    #: The file a failed run's step table and command transcript were written to; "" when none.
    diagnostics: str = ""

    @property
    def exit_code(self) -> int:
        if any(c.status == FAIL for c in self.checks):
            return 1
        if any(c.status == UNAVAILABLE for c in self.checks) or not self.checks:
            return 2
        return 0

    def table(self) -> str:
        width = max([len(c.name) for c in self.checks] + [4])
        rows = [f"{'step':>4}  {'check':<{width}}  result       detail"]
        for c in self.checks:
            rows.append(f"{c.step:>4}  {c.name:<{width}}  {c.status.upper():<11}  {c.detail}")
        verdict = {0: "PASS", 1: "FAIL", 2: "COULD NOT RUN"}[self.exit_code]
        rows.append(f"dry run {verdict}: {len(self.checks)} check(s), from {self.from_commit[:10]}")
        if self.diagnostics:
            rows.append(f"diagnostics (every step's stdout and stderr): {self.diagnostics}")
        return "\n".join(rows)

    def markdown(self) -> str:
        lines = [
            "# Upgrade dry run",
            "",
            f"Generated {self.started}. Rehearsed on a clone of `{self.source}` at "
            f"`{self.from_commit[:12]}`; new ddflow: {self.new_label}; old ddflow: "
            f"{self.old_label or 'none'}. Nothing live was touched.",
            "",
            "| step | check | result | detail |",
            "|---|---|---|---|",
        ]
        if self.diagnostics:
            lines[2:2] = [f"Every command's output: `{self.diagnostics}`.", ""]
        for c in self.checks:
            lines.append(f"| {c.step} | {c.name} | {c.status} | {c.detail.replace('|', '/')} |")
        if self.preview:
            lines += ["", "## Upgrade preview", "", "```", self.preview.rstrip(), "```"]
        return "\n".join(lines) + "\n"

    def as_data(self) -> dict[str, Any]:
        return {
            "exit": self.exit_code,
            "source": self.source,
            "from": self.from_commit,
            "new": self.new_label,
            "old": self.old_label,
            "checks": [asdict(c) for c in self.checks],
            "facts": self.facts,
            "diagnostics": self.diagnostics,
        }


class CouldNotRun(Exception):
    """The rehearsal cannot start (no git, a bad commit, a work directory inside the project)."""


def bounded(text: str, limit: int = STREAM_BOUND) -> str:
    """``text`` cut to about ``limit`` characters, the head and the tail kept."""
    if len(text) <= limit:
        return text
    half = limit // 2
    if not half:  # too small to keep two ends
        return text[: max(limit, 0)]
    return f"{text[:half]}\n... [{len(text) - 2 * half} characters cut] ...\n{text[-half:]}"
