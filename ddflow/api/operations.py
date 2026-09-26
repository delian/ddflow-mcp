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


def cadence(repo: Path, *, ran: str = "", note: str = "", agent: str = "") -> O.Outcome:
    """Which periodic passes are due? Derived from the log, so there is no state file."""
    from ..services.cadence import lessons_cadence

    log, cfg, st = _load(repo, agent)
    done_tasks = sum(1 for i in st.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in st.items.values() if i.kind == "phase" and i.state == "done")

    if ran:
        if ran == "lessons_compression":
            live = [x for x in st.lessons.values() if not x.superseded_by]
            result = json.dumps(
                {"bytes": sum(len(x.text().encode("utf-8")) for x in live), "entries": len(live)}
            )
        else:
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
        runs = st.cadences.get(name, [])
        at_last = int(runs[-1].get("result", "0") or 0) if runs else 0
        since = count - at_last
        if every > 0 and since >= every:
            due.append({"cadence": name, "since": since, "every": every, "unit": unit})
    due += lessons_cadence(st, cfg)
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

    _log, _cfg, st = _load(repo, agent)
    r = IM.verify_import(repo, st)
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
        repo, st, include_done=include_done, max_tasks=max_tasks or cfg.importer.max_tasks
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
        "notes": plan.notes,
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
