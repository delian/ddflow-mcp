"""Running a command gate: the subprocess, its output log and summary, the exit verdict and config drift."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

from ...core.records import GateOutcome
from ...infra import fsio
from ...infra import worktree as W
from .. import cmdrunner
from .defs import GateDef
from .evidence import diff_stat, digest, tree_identity
from .testcmd import suggested_test_command, summary_lines

#: Where `gate run` keeps each run's full output, under the primary's `.ddflow/`.
#: Machine-local: the directory ignores itself, so no project's .gitignore has to know.
RUNS_DIR = "runs"
#: Run logs kept per item and gate; older ones are deleted when a new one is written.
KEEP_RUN_LOGS = 10


def run_log_writer(repo: Path, item_id: str, gate: str) -> Callable[[str], str]:
    """A `keep_output` for `run_command_gate`: writes the output to
    `.ddflow/runs/<item>/<gate>-<UTC time>-<pid>.log` and returns that path relative to
    ``repo``. The newest KEEP_RUN_LOGS per item and gate are kept."""

    def keep(out: str) -> str:
        runs = fsio.ensure_ignored_dir(
            Path(repo) / ".ddflow" / RUNS_DIR,
            comment="gate run output logs: machine-local, never committed",
        )
        where = runs / item_id
        where.mkdir(exist_ok=True)
        # Imported here, not at the top (deferred, as it was before the area split).
        from ...core.clock import compact_at

        stamp = compact_at()
        path = where / f"{gate}-{stamp}-{os.getpid()}.log"
        fsio.replace_text(path, out.encode("utf-8", "replace").decode("utf-8"), fsync=False)
        # THIS gate's logs only -- `unit-*.log` would also match gate `unit-fast`'s --
        # ordered by the stamp in the name, never by mtime (coarse on some filesystems,
        # which could rank the log just written as the oldest), and never the new one.
        mine = re.compile(rf"{re.escape(gate)}-\d{{8}}T\d{{6}}(\d{{6}})?Z-\d+\.log")
        old = sorted(
            (q for q in where.iterdir() if mine.fullmatch(q.name) and q != path),
            key=lambda q: q.name[len(gate) + 1 :],
        )
        for stale in old[: max(0, len(old) - (KEEP_RUN_LOGS - 1))]:
            stale.unlink(missing_ok=True)
        return path.relative_to(repo).as_posix()

    return keep


def _unavailable_evidence(gdef: GateDef, p: cmdrunner.CommandRun) -> dict[str, Any]:
    """The evidence of a command gate whose command did not run: why, and never a failure."""
    if p.kind == cmdrunner.MISSING:
        return {
            "reason": f"executable {p.missing!r} is not on PATH -- the gate could not run. "
            f"This is NOT a failing check; install it or reconfigure the gate.",
            "command": gdef.command,
            "missing_executable": p.missing,
        }
    if p.kind == cmdrunner.TIMEOUT:
        return {
            "reason": p.reason,
            "command": gdef.command,
            "elapsed_s": round(p.elapsed_s, 1),
        }
    if p.kind == cmdrunner.NOT_FOUND:
        return {"reason": p.reason, "command": gdef.command, "exit": p.code}
    return {"reason": p.reason, "command": gdef.command}  # COULD_NOT_RUN, BUSY


#: How much of the end of a command's output the log keeps as evidence.
OUTPUT_TAIL_CHARS = 2000


def output_evidence(text: str) -> dict[str, Any]:
    """What a gate keeps about the OUTPUT of its command: a digest of all of it (so the
    output can be checked against a kept copy), its size, the tail, and the suite's own
    verdict lines from ALL of it -- a gate that reruns its failures ends on the rerun's
    "112 passed", and the tail alone hid the first pass's "115 failed" (bug Bac392907b1).
    One builder for a command ddflow ran and for output an agent recorded
    (`record --output-file`): they used to be two copies."""
    return {
        "output_digest": digest(text),
        "output_bytes": len(text),
        "tail": text[-OUTPUT_TAIL_CHARS:],
        "summary": summary_lines(text),
    }


def run_command_gate(
    gdef: GateDef,
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 0,
    keep_output: Callable[[str], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Execute a command gate. Returns (outcome, evidence).

    ``keep_output`` stores the WHOLE output and returns where (`gate run` passes
    `run_log_writer`): without it the evidence holds only the tail, and its digest
    names bytes nobody kept.

    The distinction this function exists to preserve: a command that *ran and failed*
    is ``failed``; a command that *could not run* — binary missing, cwd gone, timed out —
    is ``unavailable``. Collapsing them lets a tool that quietly stopped being installed
    read as a suite that quietly started passing.
    """
    if not gdef.is_command_gate:
        reason = "no command configured for this gate"
        hint = suggested_test_command(cwd) if gdef.id == "unit_tests" else ""
        if hint:
            reason += f" -- for this project: ddflow config --set gate.unit_tests.command '{hint}'"
        return "unavailable", {"reason": reason}
    if not cwd.exists():
        return "unavailable", {"reason": f"working directory {cwd} does not exist"}

    # No bytecode cache in either direction. CPython invalidates a `.pyc` on the
    # source's mtime and SIZE -- and an agent editing in a loop produces same-second,
    # same-size edits by accident, which leaves a stale cache that looks valid. The run
    # AFTER a patch then executes the code from BEFORE it and reports a pass about
    # source that is no longer there: the worst possible failure for a verification step.
    #
    # PYTHONDONTWRITEBYTECODE alone only stops the gate WRITING a cache; it still READ
    # the one the agent's own test run left in `__pycache__`, and executed stale code
    # (tests/test_gates.py::test_a_gate_never_runs_a_stale_pyc_someone_else_wrote).
    # Pointing PYTHONPYCACHEPREFIX at an empty directory makes the interpreter look
    # there instead, so every module compiles from the source actually on disk.
    #
    # Set AFTER os.environ, so an ambient shell variable cannot quietly undo it, and
    # BEFORE the gate's own env, so an operator can still override it deliberately.
    #
    # The cost is real, and the override is the escape hatch for it: every interpreter
    # the gate starts compiles from source (measured ~0.2s -> ~1.0s for a heavy import
    # set), and a gate that INSTALLS packages (`pip install -e . && pytest`, tox) records
    # bytecode paths inside this throwaway directory in the venv's RECORD. A gate that
    # pays too much sets `env = { PYTHONPYCACHEPREFIX = "" }` -- empty disables the
    # prefix -- and accepts the stale-cache hazard for itself, knowingly.
    with fsio.scratch_dir("ddflow-pyc-", ignore_cleanup_errors=True) as no_cache:
        full_env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(no_cache),
            **gdef.env,
            **(env or {}),
        }
        # A command gate IS a shell command line the operator wrote in gates.toml, run by the
        # one CommandRunner: it checks the executable is installed BEFORE running (under
        # `shell=True` a missing binary exits 127, indistinguishable from a suite that chose
        # to exit 127), ticks on THIS thread (the first keep-alive renewed the lease from a
        # background thread, and the log's parse cache and lock bookkeeping are
        # process-global and unlocked: roborev 827) and, on timeout, kills the whole process
        # group (Bed0f5b6d99).
        ticking = on_tick is not None and tick_s > 0
        p = cmdrunner.CommandRunner().run(
            cmdrunner.Declared(gdef.command, f"gates.toml [gate.{gdef.id}]"),
            cwd=cwd,
            env=full_env,
            timeout_s=gdef.timeout_s,
            on_tick=on_tick if ticking else None,
            tick_s=tick_s if ticking else 0,
        )
    if not p.ran:
        return "unavailable", _unavailable_evidence(gdef, p)
    out = p.output
    ev = {
        "command": gdef.command,
        "exit": p.code,
        "elapsed_s": round(p.elapsed_s, 1),
        **output_evidence(out),
        # WHICH tree this is evidence about. Without it "the tests passed" names
        # nothing: a concurrent agent can move the tree underneath a running probe, and
        # one agent editing between two gates makes the earlier gate's evidence describe
        # source that no longer exists.
        # One identity: the commit plus the tree's exact content, so `complete` can compare
        # it with the branch that landed once the worktree is gone (`tree_identity`).
        "tree_sha": tree_identity(cwd),
        # HOW MUCH this gate was looking at. `tree_sha` answers "which tree" and is
        # opaque; this answers "how big was the change", which is what makes a recorded
        # pass auditable after the fact. A review gate that passed over 4,000 changed
        # lines in two minutes is a different claim from one that passed over 12, and
        # without this the log cannot tell them apart.
        "diff_stat": diff_stat(cwd),
    }
    if keep_output is not None:
        # The full output the digest is over, so the digest can be checked. A log that
        # cannot be written is said so in the evidence; it does not change the outcome.
        try:
            ev["output_log"] = keep_output(out)
        except (OSError, ValueError) as exc:  # ValueError: an encoding the log refused
            ev["output_log_error"] = str(exc)[:200]
    outcome, why = classify_exit(gdef, p.code, out)
    if why:
        ev["reason"] = why
    if outcome == "failed":
        # Only a FAILURE is worth the git calls: a pass under drift is main's command
        # passing here, which is what the gate asks, and an unavailable already says so.
        outcome = _account_for_drift(gdef, cwd, p.code, out, ev)
    return outcome, ev


#: The `[gate.<id>]` keys that decide what a command gate RUNS and how its exit reads.
#: Only these count as drift: a reworded prompt on main must not turn a real failure on
#: an older branch into "unavailable".
_RUN_FIELDS = frozenset(
    {"command", "cwd", "env", "timeout_s", "unavailable_exits", "partial_exits",
     "require_output", "fail_output"}
)  # fmt: skip


def _run_spec(texts: Iterable[str], gate_id: str) -> dict[str, Any]:
    """The run-deciding part of `[gate.<gate_id>]` across `texts`, later winning."""
    spec: dict[str, Any] = {}
    for text in texts:
        try:
            block = (tomllib.loads(text).get("gate") or {}).get(gate_id)
        except tomllib.TOMLDecodeError:
            continue
        if isinstance(block, dict):
            spec.update(block)
    return {k: v for k, v in spec.items() if k in _RUN_FIELDS}


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except (OSError, ValueError):
        return ""


def gate_config_drift(gate_id: str, tree: Path) -> dict[str, Any]:
    """How the gate definition `load_gates` ran differs from the one `tree` commits.

    `{}` when it does not, or `tree` is the primary, or neither side COMMITTED a change
    since the fork (the difference is an uncommitted edit). Otherwise `kind` says whose
    change it is: "behind" -- the base COMMITTED a new definition since `tree` forked
    and the tree still carries the one it forked with, so its code and dependencies
    predate the command that ran -- or "own", the branch changed the definition itself,
    which merging the base cannot fix.

    Needed because the definition comes from the PRIMARY (`repo_root`) while the command
    runs in the item's tree: main switching unit_tests to `-n 48` alongside adding
    pytest-xdist failed every older branch with `unrecognized arguments: -n 48`,
    recorded as a test failure (bug B8ea7a90aea). The config-knob half of the same
    class warns "merge main" from `config._warn_unknown`.
    """
    # Function-level (deferred, as it was before the area split).
    from ...infra import tomlcfg

    try:
        primary = W.repo_root(tree)
    except (W.GitError, OSError):
        return {}
    if primary.resolve() == Path(tree).resolve():
        return {}
    # The layers `load_gates` reads. Every spec compared below carries the PRIMARY's
    # machine-local layer on top: it is git-ignored, applies whichever tree's committed
    # files it lands on, and wins -- so a committed change it overrides never ran, and
    # calling that drift turned a real failure into "unavailable" (bug
    # B-drift-ignores-local-layer).
    paths = tomlcfg.config_paths(primary, "gates.toml")
    committed = [str(p.relative_to(primary)) for p in paths[:2]]
    local = [_read_text(p) for p in paths[2:]]

    def spec(committed_texts: Iterable[str]) -> dict[str, Any]:
        return _run_spec([*committed_texts, *local], gate_id)

    ran = spec(_read_text(primary / f) for f in committed)
    here = spec(_read_text(Path(tree) / f) for f in committed)
    if ran == here:
        return {}
    # The primary's HEAD by SHA: the name `HEAD` means the tree's own HEAD inside `tree`.
    head = W.git(primary, "rev-parse", "HEAD").out
    base = W.git(primary, "rev-parse", "--abbrev-ref", "HEAD").out
    base = base if base and base != "HEAD" else head[:12] or "the primary checkout"
    fork = W.git(tree, "merge-base", "HEAD", head) if head else None
    if not (fork and fork.ok and fork.out):
        return {}

    def at(rev: str) -> dict[str, Any]:
        return spec(W.git(tree, "show", f"{rev}:{f}").out for f in committed)

    # Classified on COMMITS alone. `ran` and `here` include uncommitted edits, and
    # letting them decide mislabelled both ways: an operator's uncommitted tweak in the
    # primary on top of main's committed change hid the drift, and an agent's
    # half-made edit in the tree turned a branch that is behind into one "changing the
    # gate itself" (bug B-drift-dirty-trees).
    #
    # "behind" first: when the base committed a change, the base's definition is what
    # ran, and the tree's code predates it whatever the tree edited itself -- checking
    # "own" first recorded exactly the original bare failure for a branch that had also
    # touched, say, the timeout (bug B-drift-own-masks-behind).
    forked = at(fork.out)
    if at(head) != forked:
        kind = "behind"
    elif at("HEAD") != forked:
        kind = "own"
    else:
        # Neither side committed a change: the difference is an uncommitted edit -- an
        # operator who just set the command. Merging the base would bring nothing, and
        # the command that ran is the one configured: its failure is a failure
        # (tests/test_cli.py).
        return {}
    return {"kind": kind, "base": base, "ran": ran, "tree": here}


def _account_for_drift(gdef: GateDef, cwd: Path, code: int, out: str, ev: dict[str, Any]) -> str:
    """The outcome of a FAILED run once gate-config drift is known; notes it in `ev`.

    Behind: the failure is the tree's age, not its tests -- UNAVAILABLE, naming the
    remedy, as a tool that could not run is. Own: the failure stands (the branch's
    definition takes effect only when it merges), but says which command ran, or the
    author debugs a command their tree no longer contains.
    """
    drift = gate_config_drift(gdef.id, cwd)
    if not drift:
        return "failed"
    ev["gate_config_drift"] = drift
    base = drift["base"]
    last = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    ran = f"`{gdef.command}` exited {code}" + (f": {last[:160]}" if last else "")
    if drift["kind"] == "behind":
        ev["reason"] = (
            f"your branch is behind a gate-config change on {base}: gate {gdef.id!r} ran "
            f"{base}'s definition, which this branch's code and dependencies predate "
            f"({ran}). NOT a test failure: merge {base} into this branch, then re-run."
        )
        return "unavailable"
    ev["reason"] = (
        f"this branch changes gate {gdef.id!r}'s definition, but gates run {base}'s until "
        f"it merges: {ran}. " + (f"{ev['reason']}" if ev.get("reason") else "")
    ).strip()
    return "failed"


class Classified(NamedTuple):
    """A command gate's outcome and the reason for it ("" for a plain pass or fail)."""

    outcome: GateOutcome
    reason: str = ""


def classify_exit(gdef: GateDef, code: int, output: str) -> Classified:
    """`(outcome, reason)` for a command that RAN. The reason is "" for a plain pass/fail.

    The gate's declared exit codes and output patterns first, then the POSIX default.
    A pattern that does not compile is UNAVAILABLE naming the knob: a broken verdict
    rule decides nothing, and guessing either way would be a verdict nobody gave.
    """
    if code in gdef.unavailable_exits:
        return Classified(
            GateOutcome.UNAVAILABLE,
            (
                f"exit {code} is declared UNAVAILABLE for this gate (unavailable_exits): the "
                f"tool could not do its job. NOT a failing check."
            ),
        )
    if code in gdef.partial_exits:
        return Classified(
            GateOutcome.PARTIAL, f"exit {code} is declared PARTIAL for this gate (partial_exits)"
        )
    if code != 0:
        return Classified(GateOutcome.FAILED, "")
    try:
        if gdef.fail_output and re.search(gdef.fail_output, output, re.M):
            return Classified(
                GateOutcome.FAILED,
                f"exit 0, but the output matches fail_output /{gdef.fail_output}/",
            )
        if gdef.require_output and not re.search(gdef.require_output, output, re.M):
            return Classified(
                GateOutcome.UNAVAILABLE,
                (
                    f"exit 0, but the output lacks require_output /{gdef.require_output}/: the "
                    f"tool did not demonstrably do its job. NOT a pass."
                ),
            )
    except re.error as exc:
        return Classified(
            GateOutcome.UNAVAILABLE, f"gate {gdef.id!r} has an invalid output pattern ({exc})"
        )
    return Classified(GateOutcome.PASSED, "")


#: The executable pre-flight and the shell-word list moved to the one CommandRunner; these
#: names stay so `gates._missing_executable` and the rest keep working.
_SHELL_META = cmdrunner.SHELL_META
_SHELL_BUILTINS = cmdrunner.SHELL_WORDS
_missing_executable = cmdrunner.executable_missing
_looks_like_not_found = cmdrunner.looks_like_not_found
