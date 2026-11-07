"""The CI run: resolve the command, merge the branch's tree, and run the checks there.

Split out of ``ddflow/services/ci.py``: ``run`` and its stage machinery, from the suite
lock through the process, the scope evidence and the ``Result`` it settles on.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path

from ...config import Config
from ...infra import fsio
from ...infra import worktree as W
from .. import ci_scope as CS
from .. import suitelock as SL
from .. import workers as WK
from ..cmdrunner import TIMEOUT, CommandRunner, Declared
from ..flakes import failed_tests, failure_evidence
from ..gateenv import child_env
from ..pins import pin_files
from ..pins import regenerate as regenerate_pins
from . import admission as AD
from .precommit import DEFAULT_COMMAND, PRE_COMMIT_CONFIG, PRE_PUSH_RUN, resolve_command
from .scratch import merge_tree
from .shell import scope_to_diff, tool_missing
from .types import CONFLICT, Check, Result

_HOOK_LINE = re.compile(r"^(?P<name>.+?)\.{3,}.*?(?P<status>Passed|Failed|Skipped)\s*$")
TAIL_CHARS = 4000
TESTS_FAILED = 1  #: pytest's exit when its tests ran to the end and some failed


def parse_checks(output: str) -> list[Check]:
    """Per-hook results from pre-commit's report ("ruff check ......... Failed")."""
    checks = []
    for line in output.splitlines():
        m = _HOOK_LINE.match(line.strip())
        if m and m["status"] != "Skipped":
            checks.append(Check(m["name"].strip(), m["status"] == "Passed"))
    return checks


def _merge_base(
    repo: Path, cfg: Config, base: str, cmd: str, sha: str
) -> tuple[str, Result | None]:
    """(the ref to merge ``sha`` with, None); or ("", why that ref cannot be used)."""
    named = base or cfg.ci.base
    base = named or W.default_branch(repo)
    if W.git(repo, "rev-parse", "--verify", "--quiet", base).ok:
        return base, None
    remote = f"origin/{base}"  # origin/HEAD names a branch that may exist only as a remote ref
    if not named and W.git(repo, "rev-parse", "--verify", "--quiet", remote).ok:
        return remote, None
    return "", Result(
        "failed" if named else "unavailable",
        command=cmd,
        sha=sha,
        reason=f"base {base!r} is not a commit"
        + (" (misconfigured [ci].base or --base?)" if named else "")
        + ", so the merge result cannot be checked",
    )


def run(  # noqa: PLR0913 -- one keyword per facet of a run, all keyword-only
    repo: Path,
    cfg: Config,
    *,
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    timeout_s: int = 0,
    for_item: bool = False,
    full: bool = False,
    changed_from: str = "",
    tick: tuple[Callable[[], None], float] | None = None,
    rates: Sequence[float] = (),
) -> Result:
    """Run the CI command on `ref` merged with `base` (default: the repo's default branch).
    ``timeout_s`` replaces `[ci].timeout_s` for this run when it is positive, and bounds the
    queued phase of the host suite lock besides (the per-run override covers the queue wait,
    not only the run behind it, Ba5d5d63f12).

    ``for_item``: the run is one item's (D-test-economy-r2), so `[ci].tests_scope =
    "selected"` replaces the whole-suite hook by the tests the change reaches; ``full``
    (`ci run --full`, the merge queue's batch) always runs the whole suite.

    ``changed_from`` is the commit the change being checked began at, for the tripwire hooks of
    the default command (they run on that diff, not over every file): default ``base``.

    ``tick`` is (a callback, seconds): called that often while the command runs, on this thread:
    the caller renews its lease there (DX-long-op-renew).

    ``rates`` is what past runs measured (`admission.measured_rates`), for the decision whether
    a selected run takes the suite slot; none: the file count decides."""
    repo = Path(repo)
    cmd, why = (command.strip(), "") if command.strip() else resolve_command(repo, cfg)
    if not cmd:
        return Result("unavailable", reason=why)
    if missing := tool_missing(cmd):
        return Result(
            "unavailable",
            command=cmd,
            reason=f"{missing!r} is not installed, so nothing was checked "
            f"(install it, e.g. `uv tool install pre-commit`, or set [ci].command)",
        )
    sha = W.rev(repo, ref)
    if not sha:
        return Result("unavailable", command=cmd, reason=f"{ref!r} is not a commit")
    base, refused = _merge_base(repo, cfg, base, cmd, sha)
    if refused is not None:
        return refused
    with merge_tree(repo, sha, base, lambda: pin_files(repo, cfg)) as (tree, failure):
        if tree is None:
            status = "failed" if getattr(failure, "kind", "") == CONFLICT else "unavailable"
            return Result(status, command=cmd, sha=sha, reason=failure)
        if stale := _stale_pins(tree, cfg, base, custom=bool(command.strip())):
            return Result("failed", command=cmd, sha=sha, reason=stale)
        budget = timeout_s if timeout_s > 0 else cfg.ci.timeout_s
        cmd = _default_on_the_diff(
            repo, cfg, cmd, tree, sha, base, changed_from,
            full=full, override=bool(command.strip()),
        )
        scope = None
        if for_item and not command.strip():
            asked = "selected" if not full and cfg.ci.tests_scope == "selected" else "full"
            scope = CS.plan(cfg, tree, base, requested=asked)
        used: dict[str, dict] = {}
        full_suite = not command.strip() and _needs_suite_slot(cfg, cmd, scope, rates)
        holder = _holder(repo, ref)
        try:
            p, out_prefix, waited = _run_locked(
                cfg,
                holder,
                tree,
                cmd,
                scope,
                budget,
                tick,
                used,
                full_suite,
                lock_wait=_lock_wait(timeout_s),
            )
        except SL.SuiteBusy as busy:
            return Result(
                "unavailable", command=cmd, sha=sha, reason=str(busy),
                waited_s=round(busy.waited_s, 6),
            )  # fmt: skip
        if (unfinished := _unfinished(p, cmd, sha, scope)) is not None:
            unfinished.waited_s = waited
            return unfinished
    res = _result(p, out_prefix, cmd, sha, base, scope)
    res.waited_s = waited
    if scope is None and used:
        res.scope = {"workers": used}
    return res


def _lock_wait(timeout_s: int) -> float | None:
    """The suite-lock wait limit for one run: None (the `[ci].suite_wait_s` knob alone, what
    `SL.hold` falls back to), or ``timeout_s`` when it is a positive per-run override: the
    override caps the queued wait (this value) and is the budget of the run behind it.
    A queued run used to give up at the knob although the caller had authorised a much
    longer run, and a shorter run had no say about its queue wait either (Ba5d5d63f12)."""
    if timeout_s <= 0:
        return None
    return float(timeout_s)


def _run_locked(cfg, holder, tree, cmd, scope, budget, tick, used, full_suite, *, lock_wait=None):
    """`_execute`, holding a host suite slot when it runs the whole suite. Returns the process,
    its output prefix and the seconds spent queued for the slot (None without one). ``holder``
    is the suite-slot holder label (`_holder`); ``full_suite`` is read once at the caller --
    an empty named command is the suite, never `not cmd.strip()` on the materialized command.
    ``lock_wait`` replaces the wait limit for this hold (see `_lock_wait`)."""
    waited: float | None = None
    queued_at = time.monotonic()
    with SL.hold(cfg, holder, wait_s=lock_wait) if full_suite else nullcontext():
        if full_suite:  # the slot is held: everything before this was queueing
            waited = round(time.monotonic() - queued_at, 6)
        p, out_prefix = _execute(
            cfg, tree, cmd, scope, budget, tick, holding_suite=full_suite, used=used
        )
    return p, out_prefix, waited


def _unfinished(p, cmd: str, sha: str, scope) -> Result | None:
    """The result of a run that never reached a verdict: it timed out, or its process did not
    start (the runner is missing, the slots are taken). None when the command ran."""
    if p.kind == TIMEOUT:
        return Result(
            "unavailable",
            command=cmd,
            sha=sha,
            reason=f"{p.reason}: raise [ci].timeout_s",
            scope=_scope_evidence(scope, sha),
        )
    if not p.ran:  # the runner is missing, the slots are taken, the process did not start
        return Result(
            "unavailable",
            command=cmd,
            sha=sha,
            reason=p.reason,
            scope=_scope_evidence(scope, sha),
        )
    return None


def _stale_pins(tree: Path, cfg: Config, base: str, *, custom: bool = False) -> str:
    """Why the merge result's pinned outputs are stale, or "" (current, none, or not checkable).

    A read-only `pins regenerate --check` in the scratch tree, each recipe bounded by
    `[pins].after_merge_timeout_s`: one that cannot finish is unknown, and never fails the run.
    Not for a custom ``--command``, nor with `[ci].pins_precheck` off."""
    if custom or not cfg.ci.pins_precheck:
        return ""
    try:
        rep = regenerate_pins(tree, cfg, check=True, bounded=True)
    except Exception as exc:  # a precheck must not be what stops a run -- but it says so
        print(f"ci: the pins precheck could not run ({type(exc).__name__}: {exc})", file=sys.stderr)
        return ""
    if not rep.stale:  # a run-wide problem beside a stale pin does not hide it
        return ""
    names = ", ".join(p.name for p in rep.stale)
    return (
        f"pins stale after merging {base or 'the base'}: re-record them ({names}) with "
        "`ddflow pins regenerate`, commit, and run ci again"
    )


def _default_on_the_diff(
    repo: Path,
    cfg: Config,
    cmd: str,
    tree: Path,
    sha: str,
    base: str,
    changed_from: str,
    *,
    full: bool = False,
    override: bool = False,
) -> str:
    """``cmd`` scoped to the diff the check is about, when it is the default command. A
    whole-suite run -- ``--full``, or `[ci].tests_scope = "full"` on a run with no named
    ``--command`` -- keeps ``--all-files``; a command the operator wrote themselves (`[ci].command`,
    or a ``--command`` carrying the default phrase, with no merge range) runs as written. The
    after-merge check's resolved default (``changed_from`` names the range it added) is the one
    default that scopes: on the base itself there is no branch to compare with, so the commit just
    made is its first parent."""
    whole_suite = full or (not override and cfg.ci.tests_scope == "full")
    # This mirrors the `asked` below (`"selected" if not full and tests_scope == "selected" else
    # "full"`): both read "whole suite" as the SAME two cases only because `[ci].tests_scope` is a
    # binary {selected, full} choice. Keep them in step if the choice ever gains a third value.
    if (
        whole_suite
        or (override and not changed_from)
        or cfg.ci.command.strip()
        or DEFAULT_COMMAND not in cmd
    ):
        return cmd
    # (`merge` always says where its range began; this is for a caller that does not.)
    since = changed_from or (f"{sha}^" if W.rev(repo, base) == sha else base)
    return scope_to_diff(cmd, tree, since)


def _worker_log(scope) -> dict[str, dict]:
    """The scope's record of the worker counts used, stage -> the count and its inputs."""
    used: dict[str, dict] = {}
    if scope is not None:
        scope.workers = used
    return used


def _with_workers(
    cmd: str, cfg: Config, used: dict[str, dict], stage: str, holding_suite: bool = False
) -> str:
    """``cmd`` with ``{workers}`` replaced, the count and its inputs noted under ``stage``."""
    cmd, auto = WK.resolve(cmd, cfg, holding_suite=holding_suite)
    # the hook entry a selection is cut from says `-n auto` (one per core), with or without a
    # `{workers}` beside it
    cmd, more = WK.localize_auto(
        cmd, cfg, holding_suite=holding_suite, files=WK.count_test_files(cmd)
    )
    auto = more or auto  # what the command ends up with
    if auto:
        used[stage] = {"n": auto.n, **auto.inputs}
    return cmd


def _needs_suite_slot(cfg: Config, cmd: str, scope, rates: Sequence[float] = ()) -> bool:
    """Does this run queue for the host-wide full-suite slot? The whole suite does; a SELECTED
    run does when `admission.decide` says it is heavy, and the scope keeps why."""
    if _runs_whole_suite(cmd, scope):
        return True
    if scope is None or scope.scope != "selected":
        return False
    scope.admission = AD.decide(cfg, len(scope.tests), rates)
    return scope.admission["slot"]


def _runs_whole_suite(cmd: str, scope) -> bool:
    """The default pre-push command with its test hooks on, not narrowed to a selection."""
    if PRE_PUSH_RUN not in cmd or "SKIP=" in cmd:
        return False
    return scope is None or scope.scope != "selected"


def _holder(repo: Path, ref: str) -> str:
    return os.environ.get("DDFLOW_AGENT") or f"{ref} of {repo} (pid {os.getpid()})"


@contextmanager
def localize_hooks(
    tree: Path,
    cfg: Config,
    cmd: str,
    used: dict[str, dict],
    *,
    source=None,
    cores=None,
    holding_suite: bool = False,
) -> Iterator[str]:
    """``cmd``, or -- when the pre-commit config of ``tree`` says ``-n auto`` (one pytest worker
    per core) -- ``cmd`` reading a temporary copy that says the count this host can afford now
    (`workers.compute`; its inputs go to ``used``). The committed file is untouched: GitHub CI
    keeps ``auto``."""
    config = tree / PRE_COMMIT_CONFIG
    w = None
    if "pre-commit run" in cmd and config.is_file():
        text, w = WK.localize_auto(
            config.read_text(), cfg, source=source, cores=cores, holding_suite=holding_suite
        )
    if w is None:
        yield cmd
        return
    used["hooks"] = {"n": w.n, "replaces": "-n auto", **w.inputs}
    with fsio.temp_text(text, ".yaml") as name:
        yield cmd.replace("pre-commit run", f"pre-commit run -c {shlex.quote(str(name))}")


def _execute(
    cfg: Config, tree: Path, cmd: str, scope, budget: int, tick=None, holding_suite=False, used=None
):
    """(the run that decided the verdict, the output of the run before it): the command as
    written, or -- for a selected scope -- the hooks without the suite, then the selected
    tests on the same tree."""
    selected = scope is not None and scope.scope == "selected"
    used = _worker_log(scope) if scope is not None else (used if used is not None else {})
    cmd = _with_workers(cmd, cfg, used, "command", holding_suite=holding_suite)
    with (
        localize_hooks(tree, cfg, cmd, used, holding_suite=True)
        if holding_suite
        else nullcontext(cmd) as cmd
    ):
        return _run_stages(cfg, tree, cmd, scope, budget, tick, used, selected, holding_suite)


def _run_stages(
    cfg: Config, tree: Path, cmd: str, scope, budget: int, tick, used, selected, holding_suite=False
):
    on_tick, tick_s = tick or (None, 0)
    # The operator's own [ci].command, run as they wrote it; on timeout its whole
    # process group dies, not just the shell (Bed0f5b6d99).
    p = CommandRunner().run(
        Declared(cmd, "[ci].command"),
        cwd=tree,
        env={**child_env(cfg, ci=True), **({"SKIP": scope.skip} if selected else {})},
        timeout_s=budget,
        check_installed=False,  # checked before the scratch worktree was made
        on_tick=on_tick,
        tick_s=tick_s,
    )
    unran: set[str] = set()  # stages whose process never started: not a failure of the checks
    if selected:
        scope.unran = unran
        scope.ok["hooks"] = p.ran and p.code == 0
        if not p.ran:
            unran.add("hooks")
    if not (selected and p.ran and p.code == 0):
        if selected:
            _say_what_ran(scope, unran)
        return p, ""
    before, spent = p.output, p.elapsed_s
    stages = ((scope.command, "selected"), (scope.always_command, "always"))
    todo = [(c, label) for c, label in stages if c]
    red = None  # the selection, when it ran and failed: the guards still run after it
    for at, (raw, label) in enumerate(todo):
        cmd_ = _with_workers(raw, cfg, used, label, holding_suite=holding_suite)
        before += f"\n$ {cmd_}\n"
        p = CommandRunner().run(
            Declared(cmd_, "the pre-commit `tests` hook"),
            cwd=tree,
            env=child_env(cfg, ci=True),
            timeout_s=max(min(budget, 60), budget - int(spent)),  # what is left, 60 s at least
            check_installed=True,  # a missing runner is unavailable, not a failed test
            on_tick=on_tick,
            tick_s=tick_s,
        )
        spent += p.elapsed_s
        scope.ok[label] = p.ran and p.code == 0
        if not p.ran:
            unran.add(label)
        if label == "always":
            scope.always_s = p.elapsed_s
        if at == len(todo) - 1 or not p.ran:
            break
        red = None if scope.ok[label] else p
        before += p.output
    if red is not None:
        p = _selection_verdict(red, p, unran)
    _say_what_ran(scope, unran)
    return p, before


def _selection_verdict(red, guards, unran: set[str]):
    """The run that decides a selection which failed before ``guards`` ran: the selection's own
    exit, whatever the guards did (their pass must not read as the run's), with their output, the
    part of the log not yet in the prefix.

    `_guards_unran` reads a guard stage that ran red as one whose failures are among the failing
    ids. That holds only for a run that went to its end and lists them; a timeout, a crash or a
    collection error that interrupted the run gave no verdict on the guards it never reached.
    "always" goes to ``unran`` then, so the selection alone cannot make the run baseline-red."""
    finished = guards.code == 0 or (guards.code == TESTS_FAILED and failed_tests(guards.output))
    if not guards.ran or not finished:
        unran.add("always")
    return replace(red, out=guards.output or guards.reason, err="")


_STAGE_NAMES = {
    "hooks": "the non-test hooks",
    "selected": "the selected tests",
    "always": "the always-run guards",
}


def _say_what_ran(scope, unran: set[str] = frozenset()) -> None:
    """Make the scope's ``why`` (the line the gate records) say what ran, not what was planned:
    a failed hook stage stops the test stages, failed guards end the run, and a selected scope
    that ran no test must not read as 'selected N files'. The guards run after a selection that
    failed, so what they said is told beside it."""
    order = ("hooks", "selected", "always")
    asked = [
        "hooks",
        *(
            label
            for label, cmd in (("selected", scope.command), ("always", scope.always_command))
            if cmd
        ),
    ]
    bad = next((st for st in order if st in scope.ok and not scope.ok[st]), "")
    if not bad:
        return
    notrun = [_STAGE_NAMES[st] for st in asked if st not in scope.ok]
    verb = "could not run" if bad in unran else "failed"
    scope.why += f"; {_STAGE_NAMES[bad]} {verb}" + (
        f", so {' and '.join(notrun)} did not run" if notrun else ""
    )
    if bad == "selected" and "always" in scope.ok:
        said = "passed" if scope.ok["always"] else "failed"
        said = "gave no verdict" if "always" in unran else said
        scope.why += f"; {_STAGE_NAMES['always']} {said}"


def _scope_evidence(scope, sha: str) -> dict:
    """The recorded scope with the stages that actually ran (``ran``: stage -> passed)."""
    if scope is None:
        return {}
    ev = scope.evidence(sha)
    if used := getattr(scope, "workers", None):
        ev["workers"] = used
    if admission := getattr(scope, "admission", None):
        ev["admission"] = admission
    if scope.scope == "selected":
        # stage -> True (passed) | False (ran and failed) | "unavailable" (never started)
        unran = getattr(scope, "unran", set())
        ev["ran"] = {k: ("unavailable" if k in unran else v) for k, v in scope.ok.items()}
    return ev


def _result(p, out_prefix: str, cmd: str, sha: str, base: str, scope) -> Result:
    out = out_prefix + p.output  # a shell "not found" exit is the command's verdict: it ran
    checks = parse_checks(out)
    if out_prefix:  # the hooks passed and the selected tests ran after them
        for label, count in (("selected", len(scope.tests)), ("always", len(scope.always))):
            if label in scope.ok:
                name = f"{label} tests ({count} files)"
                checks.append(Check(name, scope.ok[label]))
    if p.code != 0 and not any(not c.ok for c in checks):
        checks.append(Check("command", False, f"exit {p.code}"))
    return Result(
        "passed" if p.code == 0 else "failed",
        checks=checks,
        sha=sha,
        merged_with=base,
        command=cmd,
        reason="" if p.code == 0 else f"{cmd} exited {p.code}",
        output_tail=out[-TAIL_CHARS:],
        output=out,
        failures=failure_evidence(out) if p.code != 0 else {},
        scope=_scope_evidence(scope, sha),
    )
