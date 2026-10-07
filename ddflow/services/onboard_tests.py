"""Onboarding test gate: find how the project tests itself, measure an honest baseline,
and propose the two gates it should declare.

The prompt (onboard.md, test-gate stage) is explicit: the baseline comes from a tree
NOBODY is editing -- a detached worktree of the default branch, never the checkout being
changed; a worker count is sized so several agents can run the gate at once and belongs
in `.ddflow/local/gates.toml`; tests already failing at the baseline become a shrink-only
known-failures list rather than a gate everyone learns to ignore; and the phase-end
`live_test` is the smallest real run of the project's entry point that FAILS when it
produces nothing.

Nothing is written here: the operator sets the proposal with `ddflow_configure`
(B-onboard-surface wires this service to the CLI and MCP; until then the render IS the
report). The baseline is not trapped into the returned object's prose either -- a
command that could not run is `ran=False`, never a passing baseline.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import proc as P
from ..infra import worktree as W

#: `2 passed, 1 failed in 3.41s` and friends; the tool's own summary vocabulary.
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
_FAILING = re.compile(r"^(FAILED|ERROR) (\S+)")
#: How much raw output the report keeps for the operator to look at.
_TAIL = 2000
#: How many failing ids the known-failures list quotes.
_KNOWN = 50
#: The shell's own "command not found" exit code.
_NOT_FOUND = 127


@dataclass(frozen=True)
class Runner:
    """How this project runs its tests, and where that was read from."""

    family: str  #: python | node | make
    command: str  #: base command WITHOUT a worker count
    evidence: str  #: the file that said so
    workers: str = ""  #: e.g. "-n 12", when parallelism applies and is declared


@dataclass
class Baseline:
    """What the suite did in a detached tree of the default branch."""

    command: str
    ran: bool
    exit_code: int
    detail: str
    counts: dict[str, int] = field(default_factory=dict)
    failing: list[str] = field(default_factory=list)
    seconds: float = 0.0
    tail: str = ""

    @property
    def green(self) -> bool:
        """Green means tests RAN and none failed: exit 0 with zero observed tests is not
        a baseline (roborev on 72ee825)."""
        passed = self.counts.get("passed", 0)
        bad = self.counts.get("failed", 0) + self.counts.get("error", 0)
        return self.ran and self.exit_code == 0 and passed > 0 and not bad and not self.failing


@dataclass(frozen=True)
class Proposal:
    """One gate the operator is asked to set, and the exact command it would carry."""

    kind: str  #: unit_tests | live_test
    command: str
    where: str  #: .ddflow/gates.toml (shared) or .ddflow/local/gates.toml (this machine)
    why: str


@dataclass
class Report:
    runner: Runner | None
    baseline: Baseline | None
    unit_tests: Proposal | None
    live_test: Proposal | None
    known_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _declares_xdist(text: str) -> bool:
    """Is pytest-xdist a declared dependency? A substring test counts a comment
    (`# no xdist here`) as a plugin that is not installed (roborev on 72ee825)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return "pytest-xdist" in text
    deps: list[str] = []

    def collect(values):
        if isinstance(values, (list, tuple)):
            deps.extend(v for v in values if isinstance(v, str))

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    collect(project.get("dependencies"))
    for group in (project.get("optional-dependencies") or {}).values():
        collect(group)
    for group in (data.get("dependency-groups") or {}).values():
        collect(group)
    return any("pytest-xdist" in dep for dep in deps)


def _text(path: Path) -> str:
    return path.read_text("utf-8", errors="replace") if path.is_file() else ""


def detect_runner(repo: Path) -> Runner | None:
    """How this project runs its tests, from its own files; None when nothing says.

    The evidence path is part of the answer: the operator is being asked to trust a
    command, and a command with no file behind it is a guess.
    """
    repo = Path(repo)
    pyproject = repo / "pyproject.toml"
    text = _text(pyproject)
    pythonish = (
        "[tool.pytest.ini_options]" in text
        or (repo / "pytest.ini").is_file()
        or (repo / "tox.ini").is_file()
        or (repo / "conftest.py").is_file()
        or ((repo / "tests").is_dir() and any((repo / "tests").glob("test_*.py")))
    )
    if pythonish:
        uvproject = (repo / "uv.lock").is_file()
        evidence = str(pyproject) if "[tool.pytest.ini_options]" in text else "tests/ + conftest"
        workers = ""
        if _declares_xdist(text):
            # Several agents run this gate at once, so a count that saturates the box is
            # the wrong answer (`auto` included); a quarter of the cores leaves room.
            workers = f"-n {max(2, (os.cpu_count() or 4) // 4)}"
        base = "uv run pytest" if uvproject else "python -m pytest"
        return Runner("python", base, evidence, workers)
    package = repo / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(_text(package)).get("scripts") or {}
        except json.JSONDecodeError:
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return Runner("node", "npm test --silent", str(package))
    makefile = repo / "Makefile"
    if re.search(r"^test:", _text(makefile), re.M):
        return Runner("make", "make test", str(makefile))
    return None


def baseline(repo: Path, command: str, *, timeout: int = 900) -> Baseline:
    """Run `command` in a DETACHED worktree of the default branch and measure it.

    Never in the checkout being changed: a tree with this session's edits measures this
    session, not the project. The detached tree is removed whatever happens.
    """
    repo = Path(repo)
    tmp = Path(tempfile.mkdtemp(prefix="ddflow-baseline-"))
    add = W.git(repo, "worktree", "add", "--detach", str(tmp), W.default_branch(repo))
    if not add.ok:
        shutil.rmtree(tmp, ignore_errors=True)
        return Baseline(
            command,
            False,
            -1,
            f"could not make a detached worktree: {add.err.strip() or add.out.strip()}",
        )
    started = time.monotonic()
    try:
        code, out = _run_bounded(command, tmp, timeout)
        seconds = time.monotonic() - started
        if code is None:
            return Baseline(command, False, -1, f"the command did not finish within {timeout}s")
        if code == _NOT_FOUND:
            # A command that could not be found measured NOTHING; "exit 127" is not a
            # baseline (critic on 215407bb).
            return Baseline(command, False, 127, "the command could not be found (exit 127)")
        summary = _summary(out)
        if code != 0 and not summary:
            # A non-zero exit with nothing a baseline recognises is a run that could not
            # be MEASURED (the runner may not exist on the default branch at all), not a
            # red suite (roborev on 72ee825).
            return Baseline(
                command,
                False,
                code,
                f"the command failed with no test counts (exit {code}); the runner may not exist on {W.default_branch(repo)}",
            )
        counts: dict[str, int] = {}
        for match in _COUNT.finditer(summary):
            counts[match.group(2).rstrip("s")] = int(match.group(1))
        failing = [m.group(2) for line in out.splitlines() if (m := _FAILING.match(line))]
        return Baseline(
            command, True, code, summary or f"exit {code}", counts, failing, seconds, out[-_TAIL:]
        )
    finally:
        W.git(repo, "worktree", "remove", "--force", str(tmp))
        shutil.rmtree(tmp, ignore_errors=True)


def _run_bounded(command: str, cwd: Path, timeout: int) -> tuple[int | None, str]:
    """(exit code, or None when it did not finish, merged output).

    The command is a shell line, so the process that must die on timeout is the whole
    GROUP: `subprocess.run` kills only the shell, and a grandchild (the runner and its
    workers) keeps the pipes open -- the bound is not enforced and orphans keep running
    against a tree that is about to be deleted (rubber_duck on 4e5160bb).
    """
    r = P.run_shell(command, timeout_s=timeout, cwd=cwd, merge_stderr=True)
    return (None if r.timed_out or r.could_not_run else r.code), r.out


def _summary(out: str) -> str:
    for line in reversed(out.splitlines()):
        if _COUNT.search(line):
            return line.strip()
    return ""


def live_test(repo: Path) -> Proposal | None:
    """The smallest real run of the project's own entry point, or None when unsure.

    Wrapped in `test -n "$(...)"` on purpose: a smoke run that prints nothing must FAIL,
    which a bare `--version` would not do (prompt, test-gate stage). The entry point and
    its flag are a proposal -- the operator confirms them.
    """
    repo = Path(repo)
    uvproject = (repo / "uv.lock").is_file()
    runner = "uv run " if uvproject else ""
    pyproject = repo / "pyproject.toml"
    text = _text(pyproject)
    scripts = re.search(r"^\[project\.scripts\]\s*$", text, re.M)
    if scripts:
        for line in text[scripts.end() :].splitlines():
            if line.startswith("["):
                break
            match = re.match(r"^([A-Za-z0-9_.-]+)\s*=", line)
            if match:
                name = match.group(1)
                return Proposal(
                    "live_test",
                    f'test -n "$({runner}{name} --version)"',
                    ".ddflow/gates.toml",
                    f"the console script {name} is the project's entry point; confirm the flag",
                )
    for main in sorted([*repo.glob("*/__main__.py"), *repo.glob("src/*/__main__.py")]):
        package = main.parent.name
        if package.startswith(".") or (main.parent / "__init__.py").is_file() is False:
            continue
        return Proposal(
            "live_test",
            f'test -n "$({runner}python -m {package} --version)"',
            ".ddflow/gates.toml",
            f"{package}/__main__.py is the project's entry point; confirm the flag",
        )
    return None


def propose(repo: Path, *, timeout: int = 900) -> Report:
    """Detect, measure and propose -- everything the operator is asked to confirm."""
    runner = detect_runner(repo)
    if runner is None:
        return Report(
            None,
            None,
            None,
            None,
            notes=[
                "no runner detected: no CI config, pyproject, package.json or Makefile said how tests run"
            ],
        )
    result = baseline(repo, runner.command, timeout=timeout)
    known = result.failing[:_KNOWN] if result.failing else []
    notes: list[str] = []
    if result.counts.get("failed") and not known:
        notes.append(
            "the run failed but the output named no failing ids; capture them by hand for the shrink-only list"
        )
    if not result.ran:
        notes.append(f"the baseline did not run: {result.detail}")
    if runner.workers:
        # The committed command carries NO worker count: one machine's size would
        # dictate it for every clone. The local file wins over it (prompt, test-gate
        # stage), and several agents run the gate at once.
        notes.append(
            f"unit_tests for THIS machine: {runner.command} {runner.workers} -> "
            f".ddflow/local/gates.toml"
        )
    elif runner.family == "python":
        notes.append(
            "parallelism needs a declared xdist dependency; adding a dev dependency changes the project -- ask"
        )
    unit = Proposal(
        "unit_tests",
        runner.command,
        ".ddflow/gates.toml",
        f"{runner.evidence} says so; the baseline measured {result.detail}",
    )
    return Report(runner, result, unit, live_test(repo), known, notes)


def render(report: Report) -> str:
    """The operator's offer: each gate, its evidence, and what confirming it means."""
    if report.runner is None:
        return report.notes[0] if report.notes else "no runner detected"
    lines = [f"runner: {report.runner.family} ({report.runner.evidence})"]
    if report.baseline is not None:
        base = report.baseline
        if base.ran and base.green:
            lines.append(
                f"baseline: green -- {base.detail}, {base.seconds:.1f}s in a detached tree"
            )
        elif base.ran:
            lines.append(
                f"baseline: RED -- {base.detail}, {base.seconds:.1f}s in a detached tree; "
                f"every item stays blocked until the shrink-only known-failures phase exists"
            )
        else:
            lines.append(f"baseline: not measured -- {base.detail}")
    if report.unit_tests:
        lines.append(f"unit_tests: {report.unit_tests.command}  [{report.unit_tests.where}]")
    if report.known_failures:
        lines.append(
            f"known failures ({len(report.known_failures)}), shrink-only: propose its own "
            f"phase (ddflow_phase_add) rather than making the gate permanently red:"
        )
        lines += [f"  {ident}" for ident in report.known_failures]
    if report.live_test:
        lines.append(f"live_test: {report.live_test.command}  [{report.live_test.where}]")
    else:
        lines.append("live_test: no entry point found; name it by hand")
    lines += [f"note: {note}" for note in report.notes]
    lines.append(
        "set them with ddflow_configure after the operator confirms: the command, the "
        "worker count, the known-failures policy, the smoke run"
    )
    return "\n".join(lines)
