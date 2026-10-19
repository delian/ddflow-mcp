"""Roadmap: phases in lanes Now / Next / Later with done/total, open tasks, Done as a count.

A phase is in NOW when any of its tasks is running or in review, NEXT when it has an open
task nothing blocks, LATER when every open task waits on something (a dependency of its
own or of an ancestor, or the blocked state), and DONE (a count only) when it is closed and
all its tasks are done. A phase with no open task -- none yet, or all done but the phase
not closed -- is NEXT (nothing waits on it: there is work, or the phase is there to close)
or LATER (it names a dependency not met). Whether one dependency is met is the scheduler's
own answer (`schedule.dep_status`, under the project's `[flow].stack` and
`[schedule].unknown_dep_policy`), so a phase counts as met when it is closed, as for `next`
(B58010c24b3). Per dependency only: what `next` judges across several (two review branches
to stack on) is not reflected here.
Abandoned tasks are not counted. Pure over the Query: built from the single-pass
children index, so a log of thousands of items renders in milliseconds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...config import Config
from ...core import progress
from ...core.flow import effective_line
from ...core.model import ABANDONED, BLOCKED, DONE, REVIEW, RUNNING, Item, State
from ...core.schedule import critical_path, dep_status, inherited_deps
from . import registry
from . import roadmap_calc as RC
from .frame import one_line
from .query import ExportError, Query

_LANE_LABELS = (
    ("now", "Now", "work in flight"),
    ("next", "Next", "ready to start"),
    ("later", "Later", "waiting on something"),
)


def waits_on(state: State, it: Item, cfg: Config) -> list[str]:
    """The ids ``it`` is waiting for, sorted (own and inherited dependencies not met)."""
    return sorted({d for _, d in inherited_deps(state, it) if not dep_status(state, d, cfg)[0]})


def _task_row(q: Query, t: Item, cfg: Config) -> dict[str, Any]:
    if t.state in (RUNNING, REVIEW):
        status, waits = t.state, []
    else:
        waits = waits_on(q.state, t, cfg)
        status = BLOCKED if (t.state == BLOCKED or waits) else "ready"
    return {
        "id": t.id,
        "title": one_line(t.title, 110),
        "status": status,
        "waits_on": waits,
        "holder": t.lease.holder if t.lease and t.state == RUNNING else "",
    }


def _settings(cfg: Config) -> tuple[list[str], int, int, bool]:
    t = cfg.export.table("roadmap")
    hide = t.get("hide", list(RC.DEFAULT_HIDE))
    hide = [hide] if isinstance(hide, str) else list(hide)
    return (
        hide,
        int(t.get("next", RC.DEFAULT_NEXT)),
        int(t.get("tasks", RC.DEFAULT_TASKS)),
        bool(t.get("show_hidden", False)),
    )


def _phase_rows(
    q: Query, f: registry.Filters, cfg: Config, hide: list[str]
) -> tuple[list[dict], dict]:
    """Every listed phase as a row (lane and rank set), and ``{done, hidden, open}`` counts."""
    seen = {"done": 0, "hidden": 0, "open": 0}
    rows: list[dict[str, Any]] = []
    for p in q.phases():
        if f.phase and p.id != f.phase:
            continue
        if p.id != f.phase and RC.hidden(p.id, p.tags, hide):  # asking for a batch shows it
            seen["hidden"] += 1
            continue
        under = q.tasks_under(p.id)
        n = progress.tally(under)  # the one phase rule: every depth, abandoned out
        if p.state == DONE and n.done == n.live:
            seen["done"] += 1
            continue
        open_rows = [_task_row(q, t, cfg) for t in under if t.state not in (ABANDONED, DONE)]
        seen["open"] += len(open_rows)
        own = waits_on(q.state, p, cfg)
        blocked_by = sorted({w for r in open_rows for w in r["waits_on"]} | set(own))
        rows.append(
            {
                "id": p.id,
                "title": one_line(p.title, 100),
                "done": n.done,
                "total": n.live,
                "percent": RC.percent(n.done, n.live),
                "counts": RC.counts(open_rows, n.done),
                "line": effective_line(q.state, p),
                "waits_on": own if not open_rows else [],
                "blocked_by": blocked_by
                if not any(r["status"] == "ready" for r in open_rows)
                else [],
                "ready": False,  # True only for a ready phase left in Later (split_lanes)
                "lane": RC.lane_of(open_rows, own if not open_rows else []),
                "all_tasks": open_rows,
            }
        )
    return rows, seen


def _phase_before(q: Query, cfg: Config, ids: set[str]) -> Callable[[str], set[str]]:
    """The phases a phase waits on: the owners of the unmet dependencies of it and its tasks."""
    owner: dict[str, str] = {}
    for pid in ids:
        owner[pid] = pid
        for t in q.tasks_under(pid):
            owner[t.id] = pid

    def before(pid: str) -> set[str]:
        waits = set(waits_on(q.state, q.state.items[pid], cfg))
        for t in q.tasks_under(pid):
            waits |= set(waits_on(q.state, t, cfg))
        return {owner[w] for w in waits if w in owner}

    return before


def _data(q: Query, f: registry.Filters) -> dict[str, Any]:
    if f.phase and (q.item(f.phase) is None or q.item(f.phase).kind != "phase"):
        raise ExportError(f"--phase {f.phase!r}: no such phase", registry.EXIT_REFUSED)
    # The project's config when the Query has a repo; the shipped defaults otherwise.
    cfg = Config.load(q.repo) if q.repo is not None else Config()
    hide, next_n, cap, show_hidden = _settings(cfg)
    rows, seen = _phase_rows(q, f, cfg, [] if show_hidden else hide)
    by_id = {r["id"]: r for r in rows}
    key = lambda i: (q.state.items[i].priority, i)  # noqa: E731
    ranked = RC.order(by_id, _phase_before(q, cfg, set(by_id)), key)
    chain = critical_path(q.state, f.phase)
    path = set(chain)
    for rank, pid in enumerate(ranked):
        r = by_id[pid]
        r["rank"] = rank
        r["on_critical_path"] = pid in path or any(t["id"] in path for t in r["all_tasks"])
    phases = [by_id[i] for i in ranked]
    now, nxt, later = RC.split_lanes(phases, next_n)
    for lane, listed in (("now", now), ("next", nxt), ("later", later)):
        for r in listed:
            tasks = r.pop("all_tasks")
            r["tasks"], r["more_tasks"] = (
                ([], len(tasks)) if lane == "later" else RC.cap_tasks(tasks, cap)
            )
    cut = f.limit or 0
    omitted = 0
    if cut:  # --limit bounds the phases listed per lane; the counts stay true
        omitted = sum(max(0, len(x) - cut) for x in (now, nxt, later))
        now, nxt, later = now[:cut], nxt[:cut], later[:cut]
    lanes = [
        {"label": label, "blurb": blurb, "phases": listed}
        for (_, label, blurb), listed in zip(_LANE_LABELS, (now, nxt, later), strict=True)
    ]
    return {
        "lanes": lanes,
        "done_phases": seen["done"],
        "hidden_phases": omitted,
        "hidden_batches": seen["hidden"],
        "open_tasks": seen["open"],
        "critical_path": [{"id": i, "title": one_line(q.state.items[i].title, 80)} for i in chain],
        "release_lines": RC.by_line(phases),
    }


registry.register(
    registry.DocKind(
        name="roadmap",
        default_target="ROADMAP.md",
        data=_data,
        update_mode=registry.WHOLE,
        filters=frozenset({"limit", "phase"}),
        schema_version=2,
        title="Roadmap: phases in Now / Next / Later lanes with progress",
    )
)
