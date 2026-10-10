"""Periodic and one-shot operations: cleanup, cadence, import.

Two exit-code rules here are load-bearing and neither is obvious from the code:

* **`import --verify` has THREE answers, and collapsing any two loses the one that
  matters.** `2` is "nothing was ever imported" — not a failure and not a pass; `1` is
  "imported, and here is what a human still has to decide"; `0` is "imported and
  consistent". That is also why `imported_anything` is a separate field from `verified`:
  `verified: true` with an empty `imported` reads as checked-and-fine to anything that
  does not also read the exit code, and over MCP exit 2 is not an error, so `_meta` was
  the only place the truth lived.
* **`cadence` is derived from the log**, so there is no state file to drift. A pass is
  "due" when the count of completed work since the last recorded run exceeds the knob.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.plain import plain
from ..infra import fsio
from ..infra import worktree as W
from ..infra.store import Store
from ..services import cleanup as CL
from ..services import companions as C
from ..services import enforce as E
from ..services import external as EX
from ..services import importer as IM
from ..services import precommit as PC
from ..services import prosepin as PP
from ..services import testselect as TS
from ..services.cadence import DueContext, due_all
from ..services.gates import load_gates, parallel_test_advice
from ..services.schedule import calendar as schedule_calendar
from ..services.schedule import count_unit, done_counts
from ._base import _load
from .lifecycle.claim import _tree_of


def cleanup(repo: Path, *, apply: bool = False, agent: str = "") -> O.Outcome:
    """Classify every ddflow worktree and branch; with `apply`, land the safe ones.

    A tree holding UNCOMMITTED work is reported and never touched, whatever `apply` says.
    That is the whole value of the sweep: it does not destroy what it found.
    """

    log, cfg, _ = _load(repo, agent)
    st = Store(repo, cfg).ensure(log)
    plan = CL.survey(repo, cfg, st)
    data: dict[str, Any] = {
        "trees": [plain(t) for t in plan.trees],
        "stale_branches": [plain(t) for t in plan.stale_branches],
        "needs_human": len(plan.needs_human),
        "actionable": len(plan.actionable),
        "applied": apply,
        "performed": [],
        "_render": {"plan": plan},
    }
    if not plan.trees and not plan.stale_branches:
        return O.nothing(
            "cleanup", "Nothing to clean up: no ddflow worktrees or branches remain.", **data
        )
    if apply:
        # The log, so each removal re-checks for a claim under the append lock.
        data["performed"] = CL.apply(repo, cfg, plan, log=log)
        # Re-plained: a tree claimed mid-sweep changed kind, and the rows must say so.
        data["trees"] = [plain(t) for t in plan.trees]
        data["stale_branches"] = [plain(t) for t in plan.stale_branches]
    return O.ok("cleanup", **data)


def due_cadences(
    repo: Path, cfg, st, *, calendar: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Every periodic pass that is due now, derived from the log, for `cadence`. (`complete
    <phase>` asks only `services.cadence.phase_overdue`, the phase-counted subset.)"""
    calendar = schedule_calendar(cfg) if calendar is None else calendar
    return due_all(DueContext(repo, cfg, st, calendar))


def cadence(repo: Path, *, ran: str = "", note: str = "", agent: str = "") -> O.Outcome:
    """Which periodic passes are due? Derived from the log, so there is no state file."""
    log, cfg, st = _load(repo, agent)
    done_tasks, done_phases = done_counts(st)
    try:
        calendar = schedule_calendar(cfg)
    except ValueError as exc:
        return O.failed("cadence", str(exc), due=[])

    if ran:
        if ran == "lessons_compression":
            live = [x for x in st.lessons.values() if not x.superseded_by]
            result = json.dumps(
                {"bytes": sum(len(x.text().encode("utf-8")) for x in live), "entries": len(live)}
            )
        else:
            # A calendar cadence is timed by the run event's own timestamp; what it
            # records here is incidental.
            result = str(done_tasks if count_unit(ran) == "tasks" else done_phases)
        log.append("cadence.ran", ran, {"result": result, "evidence": {"note": note}})
        return O.ok("cadence.ran", cadence=ran, due=[])

    due = due_cadences(repo, cfg, st, calendar=calendar)
    data: dict[str, Any] = {"due": due, "tasks_done": done_tasks, "phases_done": done_phases}
    if not due:
        return O.nothing(
            "cadence",
            f"No cadence due ({done_tasks} tasks, {done_phases} phases completed).",
            **data,
        )
    return O.ok("cadence", **data)


def import_verify(repo: Path, *, agent: str = "") -> O.Outcome:
    """Status, still-true, and did-anyone-finish-it. Three exit codes — see the module
    docstring for why none of them may be collapsed."""

    _log, cfg, st = _load(repo, agent)
    r = IM.verify_import(
        repo, st, sources=IM.sources_from(cfg), archive=tuple(cfg.importer.archive_globs)
    )
    data: dict[str, Any] = {
        "imported": r.imported,
        "total": r.total,
        "first_at": r.first_at,
        "last_at": r.last_at,
        "findings": r.findings,
        "tasks_without_globs": r.no_globs,
        "shipped_with_open_tasks": r.shipped_drift,
        "vanished_sources": [{"item": i, "source": src} for i, src in r.vanished],
        "empty_sources": r.empty_sources,
        "unstructured_provenance": r.unstructured,
        "branches_without_globs": r.no_globs_branches,
        "imported_anything": bool(r.total or r.unstructured),
        "new_since_import": [
            {"kind": f.kind, "id": f.ident, "title": f.title, "source": f.source} for f in r.drift
        ],
        "notes": r.notes,
        "verified": bool(r.total) and not r.findings,
        "_render": {"report": r},
    }
    if not r.total and not r.unstructured:
        return O.nothing("import.verify", "Nothing in this queue was imported.", **data)
    if r.findings:
        return O.failed("import.verify", f"{len(r.findings)} thing(s) left to decide", **data)
    return O.ok("import.verify", **data)


def import_project(
    repo: Path,
    *,
    apply: bool = False,
    include_done: bool = False,
    max_tasks: int = 0,
    agent: str = "",
) -> O.Outcome:
    """Propose what an existing project already has, so the queue starts where it is.

    Reads and reports by default; `apply` writes. A project adopting ddflow on day 400 has
    four hundred days of work, and a queue that starts empty tells an agent "nothing is in
    flight" about a repository with three branches in flight.
    """

    log, cfg, st = _load(repo, agent)
    # The flag overrides the knob; 0 means 'no flag given', so an operator who set
    # [importer] max_tasks in the config is not silently overruled by an argparse default
    # that looks like a choice and is not one.
    plan = IM.plan_import(
        repo,
        st,
        include_done=include_done,
        max_tasks=max_tasks or cfg.importer.max_tasks,
        sources=IM.sources_from(cfg),
        archive=tuple(cfg.importer.archive_globs),
    )
    data: dict[str, Any] = {
        "summary": plan.summary(),
        "found": [
            {
                "kind": f.kind,
                "id": f.ident,
                "title": f.title,
                "source": f.source,
                "done": f.done,
                "needs": f.needs,
                "globs": f.globs,
            }
            for f in plan.found
        ],
        "skipped_existing": plan.skipped_existing,
        "empty_sources": plan.empty_sources,
        "notes": plan.notes + plan.source_notes,
        "applied": False,
        "_render": {"plan": plan, "preview_rows": cfg.importer.preview_rows},
    }
    if apply and plan.found:
        data["applied"] = True
        data["written"] = IM.apply_import(repo, log, plan)
        return O.ok("import", **data)
    if not plan.found:
        return O.nothing("import", "Nothing to import.", **data)
    return O.ok("import", **data)


def external_sync(repo: Path, *, agent: str = "") -> O.Outcome:
    """Observe the sibling-repository items this queue depends on; record what changed.

    Exit 2 when nothing depends on another repository; exit 1 when a referenced
    repository could not be read -- its dependents stay unmet, which is the safe side,
    but the operator has to hear why.
    """

    log, cfg, st = _load(repo, agent)
    obs = EX.sync(log, cfg, repo, st)
    rows = [
        {"dep": o.dep, "state": o.state, "title": o.title, "changed": o.changed, "error": o.error}
        for o in obs
    ]
    data: dict[str, Any] = {"observed": rows}
    if not obs:
        return O.nothing("external.sync", "No item depends on another repository.", **data)
    errors = [o for o in obs if o.error]
    if errors:
        return O.failed(
            "external.sync",
            "; ".join(f"{o.dep}: {o.error}" for o in errors),
            **data,
        )
    return O.ok("external.sync", **data)


def pins(
    repo: Path,
    document: str,
    *,
    tests: tuple[str, ...] = (),
    min_chars: int | None = None,
    top: int = 10,
) -> O.Outcome:
    """Which text of an instruction file the test suite pins, and what is free (B22).

    Exit 2 when no Python test file was found: with nothing to read the pins from, every
    sentence is of unknown status, and reporting it all as free is the failure this
    exists to prevent.
    """

    # `None` is "unset", never 0: using 0 for both made an explicit `--min-needle 0`
    # the default 12, and short pins were reported free (B22-minzero).
    if min_chars is not None and min_chars < 1:
        return O.failed("pins", f"--min-needle must be at least 1, got {min_chars}")
    path = Path(document)
    if not path.is_absolute():
        path = repo / path
    if not path.is_file():
        return O.failed("pins", f"no such document: {document}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return O.failed("pins", f"cannot read {document}: {exc}")
    dirs = tests or PP.DEFAULT_TEST_DIRS
    files = PP.test_files(repo, dirs)
    if not files:
        return O.nothing(
            "pins",
            f"No Python test files under {', '.join(dirs)}: nothing says which text of "
            f"{document} is pinned, so treat all of it as pinned.",
            document=document,
        )
    rel = W.repo_relative(repo, path, as_given=True) or str(path)
    rep = PP.coverage(
        text,
        files,
        repo=repo,
        document=rel,
        min_chars=PP.MIN_NEEDLE_CHARS if min_chars is None else min_chars,
    )
    return O.ok(
        "pins",
        document=rep.document,
        chars=rep.chars,
        pinned_chars=rep.pinned_chars,
        free_chars=rep.chars - rep.pinned_chars,
        scanned=rep.scanned,
        unparsed=rep.unparsed,
        tests=rep.tests,
        pins=[{"needle": p.needle, "tests": p.tests, "lines": p.lines} for p in rep.pins],
        free=[
            {"chars": n, "lines": [a, b], "text": t[:240]} for n, a, b, t in rep.free[: max(top, 0)]
        ],
    )


def relevant_tests(
    repo: Path,
    *,
    item: str = "",
    where: Path | None = None,
    base: str = "",
    agent: str = "",
) -> O.Outcome:
    """B16: the tests the current change reaches, and a PARALLEL command to run them.

    Fast feedback while working, never a pass. With ``item`` it also says which mode the
    unit_tests gate would use right now (D-gate-economy 1) and why: the selection once the
    item's ci passed on this very tree, else the whole suite. Exit 2 when no test reaches
    the change. The tree is the item's worktree when ``item`` names one, else ``where``
    (the caller's own checkout), else the repo; the base is the item's, else the
    configured base ref, else the default branch.
    """

    _log, cfg, st = _load(repo, agent)
    tree = _caller_tree(repo, where)
    if item:
        it = st.items.get(item)
        if it is None:
            return O.failed("tests", f"no such item {item!r}")
        if it.worktree:
            tree = W.load_path(repo, it.worktree)
        base = base or it.base
    try:
        base = base or cfg.worktree.base_ref or W.default_branch(W.repo_root(tree))
    except W.GitError as exc:
        return O.failed("tests", f"{tree} is not a git checkout: {exc}")
    sel = TS.select(tree, base)
    if sel is None:
        return O.failed("tests", f"git could not say what changed in {tree} since {base}")
    gate = load_gates(repo, cfg).get("unit_tests")
    full = gate.command if gate else ""
    files = [t.path for t in sel.tests]
    gate_scope: dict[str, Any] = {}
    if item and gate is not None:
        sc = TS.unit_tests_scope(cfg, st, st.items[item], full, tree)
        gate_scope = {
            "scope": sc.scope,
            "why": sc.why,
            "tests": [{"path": t.path, "reason": t.reason} for t in sc.tests],
            "command": sc.command,
        }
    data: dict[str, Any] = {
        "tree": str(tree),
        "base": base,
        "changed": sel.changed,
        "tests": [{"path": t.path, "reason": t.reason} for t in sel.tests],
        "command": TS.run_command(full, files, tree),
        "full_suite": full,
        "advice": parallel_test_advice(full, tree) if full else "",
        "unparsed": sel.unparsed,
        "unit_tests_gate": gate_scope,
    }
    if not files:
        return O.nothing(
            "tests",
            f"No test reaches the {len(sel.changed)} changed file(s) since {base}. That is "
            "not a pass: with no test to select, the unit_tests gate runs the whole suite.",
            **data,
        )
    return O.ok("tests", **data)


def _caller_tree(repo: Path, where: Path | None) -> Path:
    """The checkout the caller is standing in. A linked worktree stays itself, where the
    repo root (and so `repo`) is the PRIMARY -- whose files and diff are not the ones the
    caller is working on."""

    return (_tree_of(where) if where else None) or repo


def _command_found(command: str, tree: Path) -> bool:
    """Whether the program ``command`` starts with can be run from ``tree`` -- where
    pre-commit runs a hook's entry, so a relative path is read against it."""

    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    if "/" in words[0]:
        prog = Path(words[0]) if Path(words[0]).is_absolute() else tree / words[0]
        return prog.is_file() and os.access(prog, os.X_OK)
    # `shutil.which`, not `cmdrunner.executable_missing`: pre-commit does not run an entry
    # through a shell, it execs `shlex.split(entry)`, so `FOO=bar tool` or a builtin like
    # `cd` can never run there however the shell would read them (B-uni-cmdrunner.2).
    return shutil.which(words[0]) is not None


def _precommit_installed(repo: Path) -> bool | None:

    try:
        entry = next((c for c in C.load(repo) if c.id == "pre-commit"), None)
    except ValueError:  # a malformed project catalogue: it cannot say
        return None
    return C.is_installed(entry)[0] if entry is not None else None


def _activation(path: Path, exists: bool, hook_types: list[str]) -> str:
    """The command that activates the config at ``path``. Plain `pre-commit install`
    honours a file's default_install_hook_types -- the generated one declares them --
    and explicit --hook-type flags would OVERRIDE that list; they are needed only for a
    file that declares none, where a plain install sets up the pre-commit hook alone."""

    try:
        text = path.read_text(encoding="utf-8") if exists else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    if not exists or re.search(r"^default_install_hook_types\s*:", text, re.M):
        return "pre-commit install"
    return "pre-commit install " + " ".join(f"--hook-type {t}" for t in hook_types)


def precommit(
    repo: Path,
    *,
    where: Path | None = None,
    ddflow_cmd: str = "ddflow",
    write: bool = False,
    agent: str = "",
) -> O.Outcome:
    """A `.pre-commit-config.yaml` proposed for this repository's stacks.

    Proposes; installs nothing. ``write`` creates the file and REFUSES (exit 3) to replace
    one that exists: which checks gate somebody's commits is theirs to decide, and a
    config they already have is exactly that decision.
    """

    _load(repo, agent)
    ddflow_cmd = ddflow_cmd.strip()
    if not ddflow_cmd:
        return O.refused("precommit", "ddflow_cmd is empty: the local hooks would run nothing")
    try:
        shlex.split(ddflow_cmd)
    except ValueError as e:  # pre-commit splits an entry the same way, on every commit
        return O.refused("precommit", f"ddflow_cmd {ddflow_cmd!r} cannot be split: {e}")
    tree = _caller_tree(repo, where)
    prop = PC.propose(tree, ddflow_cmd=ddflow_cmd)
    if prop is None:
        # Could not run: nothing was proposed, so nothing about the proposal failed.
        return O.nothing("precommit", f"git could not list the files of {tree}")
    path = PC.config_path(tree)
    data: dict[str, Any] = {
        "path": str(path),
        # A symlink counts, dangling or not: writing through one lands somewhere else.
        "exists": path.exists() or path.is_symlink(),
        "written": False,
        # The catalogue's own probe, so both say the same -- including "could not tell".
        # Through the PRIMARY on purpose: whether the program is installed is a fact
        # about this machine, not a checkout, and the primary holds the machine-local
        # config layer `ddflow companions` reads it through.
        "installed": _precommit_installed(repo),
        # The local hooks run `ddflow_cmd` with git's environment, not this one: a
        # command missing from PATH fails every commit, which reads like a refusal.
        "ddflow_cmd": ddflow_cmd,
        "ddflow_cmd_found": _command_found(ddflow_cmd, tree),
        # `hooks status` knows these hooks by "ddflow" in their command; a wrapper named
        # otherwise runs the checks but is reported NOT installed.
        "ddflow_cmd_recognised": "ddflow" in ddflow_cmd.lower(),
        # Programs the proposed hooks run from PATH that this machine lacks -> the stages
        # whose hooks would fail without them.
        "missing": {p: st for p, st in prop.requires.items() if shutil.which(p) is None},
        # ddflow's own git hooks, which `pre-commit install` would keep as <name>.legacy
        # and run beside the config's local hooks: remove them first.
        "ddflow_hooks_installed": [
            n for n in ("pre-commit", "commit-msg") if E.armed(tree, n).via == "ddflow"
        ],
        "hook_types": prop.hook_types,  #: the git hooks `pre-commit install` sets up
        "stacks": prop.stacks,
        "repos": [
            {"repo": r.url, "rev": r.rev, "hooks": [h.id for h in r.hooks], "why": r.why}
            for r in prop.repos
        ],
        "skipped": prop.skipped,
        "text": prop.text,
    }
    data["activate"] = _activation(path, data["exists"], prop.hook_types)
    if write:
        if data["exists"]:
            return O.refused(
                "precommit",
                f"{path} exists and is not replaced: run `ddflow precommit` without --write "
                "to see the proposal, and merge by hand what you want",
                **data,
            )
        # fsio's exclusive write: written aside under a unique name and LINKED into
        # place, so the link is atomic and refuses a file made meanwhile, and a write
        # failing partway never leaves a truncated config that pre-commit would run and a
        # later --write would refuse to replace. 0644: a config is read by everyone.
        try:
            fsio.atomic_write(path, prop.text, mode=0o644, exclusive=True)
        except FileExistsError:
            data["exists"] = True
            return O.refused("precommit", f"{path} appeared meanwhile; not replaced", **data)
        except OSError as e:
            return O.failed("precommit", f"could not write {path}: {e}")
        data["written"] = data["exists"] = True
        data["activate"] = _activation(path, True, prop.hook_types)
    return O.ok("precommit", **data)
