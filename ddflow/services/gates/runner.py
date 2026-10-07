"""Running a command gate: the subprocess, its output log and summary, the exit verdict and config drift."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ...infra import fsio
from ...infra import proc as P
from .defs import GateDef
from .evidence import diff_stat, digest, source_tree, tree_fingerprint
from .testcmd import suggested_test_command

#: A test runner's own verdict lines, wherever in the output they fall: pytest's
#: `=== 3 failed, 112 passed in 4.2s ===` banners (bare under `-q`), unittest's `Ran 12 tests in 0.1s`
#: and `FAILED (failures=2)` / `OK (skipped=1)`.
_VERDICT = r"(passed|failed|errors?|skipped|xfailed|xpassed|deselected)"
_SUMMARY_LINE = re.compile(
    rf"=+ .*\b({_VERDICT[1:-1]}|no tests ran)\b.* =+"
    # pytest -q: the same verdicts, with no banner
    rf"|(\d+ {_VERDICT}\b.*|no tests ran) in \d[\d.]*s\b.*"
    r"|Ran \d+ tests? in \S+"
    r"|(OK|FAILED) \(.*\)"
)
#: How many summary lines are kept: the first and last half of them when there are more.
MAX_SUMMARY_LINES = 20


def summary_lines(out: str) -> list[str]:
    """The suite's own verdict lines from the WHOLE output, not the tail."""
    found = [ln.strip() for ln in out.splitlines() if _SUMMARY_LINE.fullmatch(ln.strip())]
    if len(found) <= MAX_SUMMARY_LINES:
        return found
    half = MAX_SUMMARY_LINES // 2
    return [*found[:half], f"... {len(found) - 2 * half} more ...", *found[-half:]]


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
        # Imported here, not at the top: every top-level name of a gates module is
        # re-exported by the package (tests/test_module_splits.py).
        from ...core.clock import compact_at

        stamp = compact_at()
        path = where / f"{gate}-{stamp}-{os.getpid()}.log"
        path.write_text(out, "utf-8", errors="replace")
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


def _run_ticking(
    command: str,
    cwd: str,
    env: dict[str, str],
    timeout_s: float,
    on_tick: Callable[[], None],
    tick_s: float,
) -> subprocess.CompletedProcess:
    """Run a shell command to completion, calling `on_tick` every `tick_s` meanwhile.

    On THIS thread. The first keep-alive for long gates renewed the lease from a
    background thread, and the event log's parse cache and lock bookkeeping are
    process-global and unlocked because nothing in this process was concurrent -- a
    renewal slower than the join timeout could race the caller's own write (roborev
    827). Polling keeps the process single-threaded. Raises TimeoutExpired like
    `subprocess.run`, after killing the command's whole process group (Bed0f5b6d99).
    """
    return P.run_shell(
        command,
        timeout=timeout_s,
        on_tick=on_tick,
        tick_s=tick_s,
        cwd=cwd,
        env=env,
        text=True,
    )


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

    # Pre-flight the executable. Under `shell=True` a missing binary exits 127, which
    # is indistinguishable from a test suite that chose to exit 127 -- so a tool that
    # quietly stopped being installed would read as a suite that ran and failed.
    # Resolving it BEFORE running removes the ambiguity at the source. Only attempted
    # for a simple leading token; anything with shell syntax up front is the operator
    # deliberately writing shell, and is left alone.
    missing = _missing_executable(gdef.command)
    if missing:
        return "unavailable", {
            "reason": f"executable {missing!r} is not on PATH -- the gate could not run. "
            f"This is NOT a failing check; install it or reconfigure the gate.",
            "command": gdef.command,
            "missing_executable": missing,
        }
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
    with tempfile.TemporaryDirectory(prefix="ddflow-pyc-", ignore_cleanup_errors=True) as no_cache:
        full_env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": no_cache,
            **gdef.env,
            **(env or {}),
        }
        start = time.time()
        try:
            if on_tick is not None and tick_s > 0:
                p = _run_ticking(gdef.command, str(cwd), full_env, gdef.timeout_s, on_tick, tick_s)
            else:
                # A command gate IS a shell command line the operator wrote in gates.toml;
                # on timeout its whole process group dies, not just the shell (Bed0f5b6d99).
                p = P.run_shell(
                    gdef.command, timeout=gdef.timeout_s, cwd=str(cwd), env=full_env, text=True
                )
        except subprocess.TimeoutExpired:
            return "unavailable", {
                "reason": f"timed out after {gdef.timeout_s}s",
                "command": gdef.command,
                "elapsed_s": round(time.time() - start, 1),
            }
        except (OSError, ValueError) as exc:
            return "unavailable", {"reason": f"could not execute: {exc}", "command": gdef.command}
    out = (p.stdout or "") + (p.stderr or "")
    # Belt and braces for the compound-command case the pre-flight cannot inspect
    # (pipes, &&, subshells): POSIX reserves 127 for "command not found" and 126 for
    # "found but not executable", and the shell says so on stderr.
    if p.returncode in (126, 127) and _looks_like_not_found(p.stderr or ""):
        return "unavailable", {
            "reason": f"shell reported exit {p.returncode} (command not found / not "
            f"executable): {(p.stderr or '').strip()[:200]}",
            "command": gdef.command,
            "exit": p.returncode,
        }
    ev = {
        "command": gdef.command,
        "exit": p.returncode,
        "elapsed_s": round(time.time() - start, 1),
        "output_digest": digest(out),
        "output_bytes": len(out),
        "tail": out[-2000:],
        # The suite's own verdict lines from ALL of it: a gate that reruns its failures
        # ends on the rerun's "112 passed", and the tail alone hid the first pass's
        # "115 failed" (bug Bac392907b1).
        "summary": summary_lines(out),
        # WHICH tree this is evidence about. Without it "the tests passed" names
        # nothing: a concurrent agent can move the tree underneath a running probe, and
        # one agent editing between two gates makes the earlier gate's evidence describe
        # source that no longer exists.
        "tree_sha": tree_fingerprint(cwd),
        # ...and its CONTENT, independent of which commit it sits on: what `complete`
        # compares against the branch that landed once the worktree is gone.
        "source_tree": source_tree(cwd),
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
    outcome, why = classify_exit(gdef, p.returncode, out)
    if why:
        ev["reason"] = why
    if outcome == "failed":
        # Only a FAILURE is worth the git calls: a pass under drift is main's command
        # passing here, which is what the gate asks, and an unavailable already says so.
        outcome = _account_for_drift(gdef, cwd, p.returncode, out, ev)
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
    from ...infra import tomlcfg
    from ...infra import worktree as W

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


def classify_exit(gdef: GateDef, code: int, output: str) -> tuple[str, str]:
    """`(outcome, reason)` for a command that RAN. The reason is "" for a plain pass/fail.

    The gate's declared exit codes and output patterns first, then the POSIX default.
    A pattern that does not compile is UNAVAILABLE naming the knob: a broken verdict
    rule decides nothing, and guessing either way would be a verdict nobody gave.
    """
    if code in gdef.unavailable_exits:
        return "unavailable", (
            f"exit {code} is declared UNAVAILABLE for this gate (unavailable_exits): the "
            f"tool could not do its job. NOT a failing check."
        )
    if code in gdef.partial_exits:
        return "partial", f"exit {code} is declared PARTIAL for this gate (partial_exits)"
    if code != 0:
        return "failed", ""
    try:
        if gdef.fail_output and re.search(gdef.fail_output, output, re.M):
            return "failed", f"exit 0, but the output matches fail_output /{gdef.fail_output}/"
        if gdef.require_output and not re.search(gdef.require_output, output, re.M):
            return "unavailable", (
                f"exit 0, but the output lacks require_output /{gdef.require_output}/: the "
                f"tool did not demonstrably do its job. NOT a pass."
            )
    except re.error as exc:
        return "unavailable", f"gate {gdef.id!r} has an invalid output pattern ({exc})"
    return "passed", ""


_SHELL_META = set(";|&<>()$`\n")


def _missing_executable(command: str) -> str:
    """The leading executable of ``command`` if it is simple and absent, else "".

    Returns "" (meaning "no opinion") for anything that is not a bare leading token:
    an assignment prefix, a pipeline, a subshell, a builtin. Being sure only about
    the easy case is the point -- a heuristic that guessed at compound shell would
    produce false UNAVAILABLEs, which stall a pipeline as surely as a false pass
    corrupts one.
    """
    cmd = command.strip()
    if not cmd or cmd[0] in _SHELL_META:
        return ""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    if not tokens:
        return ""
    head = tokens[0]
    if any(ch in _SHELL_META for ch in head) or "=" in head:
        return ""
    if head in _SHELL_BUILTINS or "/" in head:
        return ""  # builtins have no PATH entry; paths are checked by exec
    return "" if shutil.which(head) else head


#: Builtins that legitimately have no PATH entry. `exit`/`true`/`false` appear in real
#: gate configs and in this project's own tests.
_SHELL_BUILTINS = frozenset(
    {
        "exit",
        "true",
        "false",
        "cd",
        "echo",
        "test",
        "[",
        ":",
        "set",
        "unset",
        "export",
        "eval",
        "source",
        ".",
        "read",
        "wait",
        "trap",
        "shift",
        "return",
    }
)


def _looks_like_not_found(stderr: str) -> bool:
    low = stderr.lower()
    return "not found" in low or "no such file or directory" in low or "permission denied" in low
