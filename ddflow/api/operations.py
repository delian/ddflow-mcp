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
import time
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.plain import plain
from ._base import _load


def cleanup(repo: Path, *, apply: bool = False, agent: str = "") -> O.Outcome:
    """Classify every ddflow worktree and branch; with `apply`, land the safe ones.

    A tree holding UNCOMMITTED work is reported and never touched, whatever `apply` says.
    That is the whole value of the sweep: it does not destroy what it found.
    """
    from ..infra.store import Store
    from ..services import cleanup as CL

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
        data["performed"] = CL.apply(repo, cfg, plan)
    return O.ok("cleanup", **data)


def _calendar(cfg) -> dict[str, float]:
    """`[cadence] every_days` as name -> days. Raises ValueError naming the knob for an
    entry that is not `name=<positive number>`: one typo raised a bare float() error,
    and an entry without `=` was DROPPED -- a weekly pass never reported due, and
    nothing said the knob was ignored (roborev 830)."""
    out: dict[str, float] = {}
    for spec in cfg.cadence.every_days:
        name, sep, days = spec.partition("=")
        try:
            value = float(days) if sep and name.strip() else 0.0
        except ValueError:
            value = 0.0
        if value <= 0:
            raise ValueError(
                f"[cadence] every_days entry {spec!r} is not `name=days` with a positive "
                f'number of days (e.g. "bug_hunt=7")'
            )
        out[name.strip()] = value
    return out


def _calendar_due(
    st, cfg, now: float | None = None, calendar: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Calendar cadences not recorded as run within their period -- or ever."""
    from ..core.progress import epoch

    now = time.time() if now is None else now
    due = []
    for name, days in (calendar if calendar is not None else _calendar(cfg)).items():
        runs = st.cadences.get(name, [])
        # The NEWEST run by its own timestamp, not the last in fold order: the log is
        # ordered by Lamport clock, and two machines' runs can fold older-last
        # (rubber-duck).
        last = max((epoch(r["at"]) for r in runs), default=0.0)
        age_days = (now - last) / 86400 if last else None
        if age_days is None or age_days >= days:
            due.append(
                {
                    "cadence": name,
                    "since": "never" if age_days is None else f"{age_days:.1f} days",
                    "every": days,
                    "unit": "days",
                }
            )
    return due


def cadence(repo: Path, *, ran: str = "", note: str = "", agent: str = "") -> O.Outcome:
    """Which periodic passes are due? Derived from the log, so there is no state file."""
    from ..services.cadence import lessons_cadence

    log, cfg, st = _load(repo, agent)
    done_tasks = sum(1 for i in st.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in st.items.values() if i.kind == "phase" and i.state == "done")
    try:
        calendar = _calendar(cfg)
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
            result = str(
                done_tasks if ran in ("integration_tests", "dedupe_sweep") else done_phases
            )
        log.append("cadence.ran", ran, {"result": result, "evidence": {"note": note}})
        return O.ok("cadence.ran", cadence=ran, due=[])

    due: list[dict[str, Any]] = []
    for name, every, unit, count in (
        ("integration_tests", cfg.cadence.integration_tests_every_tasks, "tasks", done_tasks),
        ("dedupe_sweep", cfg.cadence.dedupe_sweep_every_tasks, "tasks", done_tasks),
        (
            "architecture_review",
            cfg.cadence.architecture_review_every_phases,
            "phases",
            done_phases,
        ),
        ("mutation_tests", cfg.cadence.mutation_tests_every_phases, "phases", done_phases),
        ("lessons_pass", cfg.cadence.lessons_pass_every_phases, "phases", done_phases),
    ):
        if name in calendar:
            # The calendar entry of the same name REPLACES this pass; without the skip
            # it would also fall due by completions, reported twice under one name.
            continue
        runs = st.cadences.get(name, [])
        at_last = int(runs[-1].get("result", "0") or 0) if runs else 0
        since = count - at_last
        if every > 0 and since >= every:
            due.append({"cadence": name, "since": since, "every": every, "unit": unit})
    due += lessons_cadence(st, cfg)
    due += _calendar_due(st, cfg, calendar=calendar)
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
    from ..services import importer as IM

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
    from ..services import importer as IM

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
    from ..services import external as EX

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
    from ..services import prosepin as PP

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
    rel = path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path)
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
