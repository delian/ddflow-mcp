"""`ddflow plan check`: mechanical checks on a PROPOSED plan, before anything is filed.

The checks themselves are `services/plancheck.py` (pure, no I/O); this is the api around
them: read the plan, give the checks the repository's tracked files and the queue's
duplicate hunt, and report. `ddflow plan check -` reads the plan from stdin.

Findings are the whole point, so they are a FAILURE (exit 1) with the findings as data; a
plan that passes is exit 0 and carries its waves. A plan that cannot be read at all is a
REFUSAL (exit 3): "could not tell" is never a clean plan.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ..config import Config
from ..core import outcome as O
from ..core.records import WORK_KINDS, Item, State
from ..infra import git as G
from ..services import items as IT
from ..services import leases as L
from ..services import plan_apply as PA
from ..services import plancheck as PC
from ..services import planlint as PL
from ..services import priority as PRI
from ._base import _load
from ._globwarn import missing_globs
from .items import ItemEdit
from .items import update as item_update


def _created(glob: str, created: set[str]) -> bool:
    """Is ``glob`` a path a plan item creates, or inside a directory one creates? A created
    path is a directory only when it ends in '/' (`creates = ["newpkg/"]`): a name alone
    cannot be told from a file, and nothing can exist under a file, so it covers itself only."""
    return any(
        glob == c or (c.endswith("/") and (glob == c[:-1] or glob.startswith(c))) for c in created
    )


def _items(data: Any) -> dict[str, Any] | None:
    """The plan as {id: item} from whatever the document holds, or None if it holds none."""
    if isinstance(data, list):
        return {str(t["id"]): t for t in data if isinstance(t, dict) and t.get("id")}
    if not isinstance(data, dict):
        return None
    for key in ("items", "tasks", "phases"):
        if isinstance(data.get(key), dict):
            return {str(k): v for k, v in data[key].items()}
        if isinstance(data.get(key), list):
            return {str(t["id"]): t for t in data[key] if isinstance(t, dict) and t.get("id")}
    # A plan may be the mapping itself. An item is one that says what it needs or touches;
    # anything else in the document (a title, a version) is not an item and is left out.
    plan = {
        str(k): v
        for k, v in data.items()
        if isinstance(v, dict) and ("needs" in v or "globs" in v or "acceptance" in v)
    }
    return plan or None


def _parse(text: str, source: str) -> dict[str, Any] | str:
    """The plan, or a sentence saying why the document is not one."""
    try:
        data: Any = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        try:
            data = json.loads(text)
        except ValueError as exc:
            return f"{source} is neither TOML nor JSON ({exc})"
    plan = _items(data)
    if plan is None:
        return (
            f"{source} holds no items: expected {{id = {{ needs = [...], globs = [...] }}}} "
            f"(or a list under `items`/`tasks`/`phases`)"
        )
    return plan


def _tracked(repo: Path) -> list[str]:
    """The repository's tracked files, for the glob that matches more than it reads as.

    Empty when git cannot list them: the check then simply does not make that comparison,
    and says so in nothing -- a plan is not refused because a listing failed.
    """
    return G.files(repo, "tracked") or []


def _read(source: str, kind: str = "plan.check") -> tuple[dict[str, Any] | None, O.Outcome | None]:
    """The plan at ``source`` ("-" for stdin), or the outcome saying why there is none."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    except OSError as exc:
        return None, O.failed(kind, f"cannot read the plan: {exc}")
    plan = _parse(text, source)
    if isinstance(plan, str):
        return None, O.refused(kind, plan)
    return plan, None


_WORK = ("task", "bug")


def _hunt(repo: Path, log, cfg: Config, st: State, own: set[str]):
    """The duplicate hunt as `plancheck.check`'s ``similar``: the ids already in the queue
    that the add-time check (``_dedupe``) would ask about, other than the plan's own."""

    def similar(title: str, body: str) -> list[str]:
        rec = DD.Record(kind="task", event_kind="task.added", rid="", title=title, body=body)
        found, shown, _unavailable = DD._assess(repo, log, cfg, st, rec)
        if found.action not in ("ask", "warn"):
            return []
        # Work that may BE the same work: a research note or a decision that shares its
        # words is context, not a duplicate of a task.
        return [str(r["id"]) for r in shown if r["id"] not in own and r.get("kind") in _WORK]

    return similar


def _queue(repo: Path, agent: str = ""):
    """(log, cfg, state), or None when the queue cannot be read: the caller refuses then
    ("could not tell" is never a clean plan)."""
    try:
        return _load(repo, agent)
    except (OSError, ValueError, LookupError):
        return None


def _report(repo: Path, plan: dict[str, Any], queue) -> tuple[PC.Report, Config]:
    tracked = _tracked(repo)
    log, cfg, st = queue
    similar = _hunt(repo, log, cfg, st, set(plan))
    return PC.check(plan, tracked=tracked, similar=similar, known=PA.live_ids(st)), cfg


def _count(report: PC.Report, source: str) -> str:
    kinds = ", ".join(sorted({f.kind for f in report.findings}))
    return f"{len(report.findings)} finding(s) in {source}: {kinds}"


def plan_check(repo: Path, source: str) -> O.Outcome:
    """Check the plan at ``source`` ("-" for stdin) and report what to fix before filing."""
    plan, bad = _read(source)
    if plan is None:
        assert bad is not None
        return bad
    queue = _queue(repo)
    if queue is None:
        # "Could not tell" is never a clean plan: a `needs` outside the plan is a queued id
        # or a typo, and without the queue the two cannot be told apart.
        return O.refused("plan.check", "the queue cannot be read, so the plan cannot be checked")
    report, cfg = _report(repo, plan, queue)
    tracked = _tracked(repo)
    # Warnings, not findings: a path the plan does not find may be a file it creates, and a
    # plan item says so with `creates = [...]` (B-plan-check-new-file-warnings-noise): a path
    # ANY item creates is expected missing, wherever the others list it.
    created = {str(c) for v in plan.values() for c in PA._list(v, "creates")}
    warnings = [
        {"id": iid, "detail": w}
        for iid in sorted(plan)
        for w in missing_globs(
            repo,
            cfg,
            [str(g) for g in plan[iid].get("globs", []) if not _created(str(g), created)],
            tracked,
        )
    ]
    warnings += [
        {"id": iid, "detail": w}
        for iid in sorted(plan)
        if "priority" in plan[iid]
        and (
            w := PRI.left_generic(cfg, iid, PA._list(plan[iid], "tags"), int(plan[iid]["priority"]))
        )
    ]
    data: dict[str, Any] = {
        "source": source,
        "clean": report.clean,
        **report.as_dict(),
        "items": len(plan),
        "warnings": warnings,
    }
    if report.findings:
        return O.failed("plan.check", _count(report, source), **data)
    return O.ok("plan.check", **data)


def _clashes(st: State, plan: dict[str, Any], plan_id: str) -> list[str]:
    """Plan ids already in the queue that are NOT this plan's earlier filing: filing over
    them would overwrite an item, so the plan is refused."""
    mine = set(PA.filed_by(st, plan_id))
    # Removed ids too: re-filing one needs `readd`, which a plan does not say.
    return sorted(i for i in plan if i in st.items and i not in mine)


def _missing_parents(st: State, plan: dict[str, Any]) -> list[str]:
    """Items whose parent is neither in the plan nor LIVE in the queue (a removed one
    cannot hold a task)."""
    live = set(PA.live_ids(st))
    return sorted(
        f"{i} -> {p}"
        for i, it in plan.items()
        if (p := str(it.get("parent") or "")) and p not in plan and p not in live
    )


def _file(log, st: State, cfg: Config, plan: dict[str, Any], plan_id: str) -> tuple[list[str], str]:
    """Write the plan's items; (the ids written, the problem). The caller holds the lock.

    An item this plan already filed is skipped, so a run killed half-way is completed by
    running the same apply again. A task is written PROPOSED in its own `task.added` event
    (there is no window in which it exists ready).
    """
    written: list[str] = []
    live = set(PA.live_ids(st))
    for iid in PA.order(plan):
        if iid in live:
            continue
        item = plan[iid]
        if PA.kind_of(item) == "phase":
            data = {
                "title": str(item.get("title", iid)),
                "needs": PA._list(item, "needs"),
                "globs": PA._list(item, "globs"),
                "body": PA.body_of(item),
                "tags": [*PA._list(item, "tags"), f"{PA.LABEL}{plan_id}"],
                "priority": int(item.get("priority", 100)),
                "line": "",
            }
            log.append("phase.added", iid, data)
            st.items[iid] = Item(id=iid, kind="phase", title=data["title"], tags=data["tags"])
        else:
            added = IT.add_task(
                log,
                st,
                cfg,
                PA.draft_of(iid, item, plan_id),
                dedupe=f"filed by plan apply {plan_id}: the plan check ran the duplicate hunt",
            )
            if not added.ok:
                return written, added.problem
        written.append(iid)
    return written, ""


def plan_apply(
    repo: Path,
    source: str,
    *,
    apply: bool = False,
    plan_hash: str = "",
    plan_id: str = "",
    agent: str = "",
) -> O.Outcome:
    """File the plan at ``source`` as PROPOSED tasks, atomically; a dry run by default.

    Refused when `plan check` has findings (exit 1, nothing written). The dry run reports
    what would be filed and the plan's hash. ``apply`` files it, but only against that hash
    (``plan_hash``): a plan edited after it was read is a STALE plan and is refused (exit 3).
    Everything is decided and written under the log lock, so no other agent files between
    the check and the write, and an item already filed by this plan is skipped so a killed
    run is finished by running it again.
    """
    plan, bad = _read(source, "plan.apply")
    if plan is None:
        assert bad is not None
        return bad
    digest = PA.plan_hash(plan)
    pid = plan_id or PA.default_plan_id(digest)
    log, cfg, st = _load(repo, agent)
    base: dict[str, Any] = {"source": source, "plan": pid, "hash": digest, "items": len(plan)}
    report, _cfg = _report(repo, plan, (log, cfg, st))
    no = _refusal(source, plan, pid, st, report, base)
    if no is not None:
        return no
    live = set(PA.live_ids(st))
    waves = report.as_dict()["waves"]
    if not apply:
        return O.ok(
            "plan.apply",
            dry_run=True,
            would_file=[i for i in PA.order(plan) if i not in live],
            waves=waves,
            hint=f"ddflow plan apply {source} --apply --hash {digest}",
            **base,
        )
    if plan_hash != digest:
        return O.refused(
            "plan.apply",
            f"stale plan: the plan now hashes to {digest}, not {plan_hash or '(none given)'}. "
            f"Read the dry run again, then `ddflow plan apply {source} --apply --hash {digest}`",
            **base,
        )
    return _commit(log, cfg, plan, pid, waves, base)


def _refusal(source, plan, pid, st, report, base) -> O.Outcome | None:
    """Why the plan cannot be filed into ``st`` (nothing is written), or None."""
    if report.findings:
        return O.failed(
            "plan.apply",
            f"{_count(report, source)}; fix the plan (`ddflow plan check {source}`)",
            findings=[f.as_dict() for f in report.findings],
            **base,
        )
    if clash := _clashes(st, plan, pid):
        return O.refused(
            "plan.apply",
            f"{', '.join(clash)} already in the queue: filing would overwrite an item",
            clashes=clash,
            **base,
        )
    if broken := PA.problems(plan, pid):
        return O.failed("plan.apply", "; ".join(broken), **base)
    if orphans := _missing_parents(st, plan):
        return O.failed(
            "plan.apply", f"parent not in the plan or the queue: {', '.join(orphans)}", **base
        )
    return None


def _commit(log, cfg, plan, pid, waves, base) -> O.Outcome:
    """Decide again under the log lock, from the log as it is NOW, and write."""
    with log.transaction():
        st = L._fold(log)
        live = set(PA.live_ids(st))
        changed = (
            _clashes(st, plan, pid)
            + _missing_parents(st, plan)
            + [
                d
                for i in plan
                for d in PA._list(plan[i], "needs")
                if d not in plan and d not in live
            ]
        )
        if changed:
            return O.refused(
                "plan.apply", f"the queue changed while applying: {', '.join(changed)}", **base
            )
        written, problem = _file(log, st, cfg, plan, pid)
    if problem:
        return O.failed("plan.apply", f"{problem} (filed so far: {', '.join(written)})", **base)
    return O.ok("plan.apply", dry_run=False, filed=written, waves=waves, **base)


def plan_confirm(
    repo: Path, plan_id: str, *, yes: bool = False, reason: str = "", agent: str = ""
) -> O.Outcome:
    """Release the tasks plan ``plan_id`` proposed. The operator's act, so it needs
    ``yes`` and a ``reason`` (the same consent every operator-only step takes); exit 2
    when nothing of the plan is waiting."""
    log, _cfg, st = _load(repo, agent)
    waiting = PA.proposed_by(st, plan_id)
    if not waiting:
        return O.nothing(
            "plan.confirm", f"nothing proposed by plan {plan_id} is waiting", plan=plan_id
        )
    if not (yes and reason.strip()):
        return O.refused(
            "plan.confirm",
            f"{len(waiting)} task(s) wait for the operator's word: "
            f'{", ".join(waiting)}. Confirm with --yes --reason "<why>"',
            plan=plan_id,
            waiting=waiting,
        )
    with log.transaction():
        st = L._fold(log)  # decided under the lock: a second confirm finds nothing
        waiting = PA.proposed_by(st, plan_id)
        for iid in waiting:
            log.append("item.unblocked", iid, {"note": f"plan {plan_id} confirmed: {reason}"})
    if not waiting:
        return O.nothing("plan.confirm", f"plan {plan_id} was confirmed meanwhile", plan=plan_id)
    return O.ok("plan.confirm", plan=plan_id, released=waiting)


def _snapshot(st: State, since: str = "", phase: str = "") -> tuple[dict[str, Any], list[str]]:
    """(`snap`, `scope`): every live item as the lint's plain view, and the ids to lint.

    Everything live is in the snapshot -- settled items carry reachability THROUGH them --
    and only live WORK_KINDS that are not settled are linted, narrowed by ``phase`` (the
    item's ancestor chain contains it) and ``since`` (filed on or after; an item without a
    date cannot be told to be since anything, so it stays out rather than silently claims).
    """
    live = st.live_by_id()
    snap = {
        iid: {
            "id": iid,
            "kind": it.kind,
            "state": it.state,
            "title": it.title,
            "body": it.body,
            "needs": [str(n) for n in it.needs],
            "globs": [str(g) for g in it.globs],
            "tags": [str(t) for t in it.tags],
            "priority": int(it.priority),
            "terminal": it.terminal,
        }
        for iid, it in live.items()
    }
    by_parent = {iid: it.parent for iid, it in live.items()}
    scope = [
        iid
        for iid, it in live.items()
        if it.kind in WORK_KINDS
        and not it.terminal
        and (not phase or any(p == phase for p in _above(live, by_parent, iid)))
        and (not since or (it.created_at and it.created_at[:10] >= since[:10]))
    ]
    return snap, scope


def _above(live: dict[str, Item], by_parent: dict[str, str], iid: str) -> list[str]:
    """``iid``'s ancestor chain (each parent once; cycles quit early: a broken parent ring
    must not loop the lint that diagnoses bad graphs)."""
    seen: set[str] = set()
    out: list[str] = []
    while True:
        up = by_parent.get(iid, "")
        if not up or up in seen:
            return out
        seen.add(up)
        out.append(up)
        iid = up
        if up not in live:
            return out


def _count_lint(report: PL.Report) -> str:
    from collections import Counter

    kinds = Counter(f.kind for f in report.findings)
    label = ", ".join(f"{k}: {n}" for k, n in sorted(kinds.items()))
    return f"{len(report.findings)} finding(s) in {report.items} live item(s): {label}"


def _apply_fixes(repo: Path, agent: str, report: PL.Report) -> dict[str, Any]:
    """Run the high-confidence fixes (`Finding.fix`); each outcome in `applied` or
    `skipped`, so a refused edit says so rather than silently not happening."""
    applied: list[str] = []
    skipped: list[str] = []
    # Findings are sorted (kind, ids): a stable order for the events and the report.
    for f in report.findings:
        if not f.fix:
            continue
        what = f"{f.kind} {' '.join(f.ids)}"
        if "needs_add" in f.fix:
            later, earlier = f.fix["needs_add"]
            out = item_update(repo, later, ItemEdit(add_needs=[earlier]), agent=agent)
        elif "needs_remove" in f.fix:
            out = item_update(
                repo, f.ids[0], ItemEdit(remove_needs=f.fix["needs_remove"]), agent=agent
            )
        else:  # {"priority": n}
            out = item_update(
                repo, f.ids[0], ItemEdit(priority=int(f.fix["priority"])), agent=agent
            )
        (applied if out.exit == O.OK else skipped).append(
            what if out.exit == O.OK else f"{what}: {out.reason}"
        )
    return {"applied": applied, "skipped": skipped}


#: Scope items up to this many get the add-time duplicate hunt. One hunt call costs ~1s
#: on this queue (index open + ensure per record), so a whole-queue run would buy 25+
#: minutes of dedupe for 1.7k items; beyond the cap the duplicates check is SKIPPED and
#: the outcome says so -- a --phase/--since run buys it back.
_DUPE_HUNT_MAX = 300


def plan_lint(
    repo: Path,
    *,
    since: str = "",
    phase: str = "",
    fix: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Lint the whole open queue: `plan check`'s mechanical checks, read FROM the queue.

    Findings are a FAILURE (exit 1) with the findings as data -- they are the point. A
    queue with nothing live to lint is nothing to do (exit 2). ``--fix`` applies only the
    high-confidence findings (`planlint`'s docstring: rule-forced ordering edges,
    stale-needs removals, priority bands) and reports each applied/refused edit.
    """
    log, cfg, st = _load(repo, agent)
    snap, scope = _snapshot(st, since=since, phase=phase)
    if not scope:
        where = f" under {phase}" if phase else f" since {since}" if since else ""
        return O.nothing("plan.lint", f"no live work{where} to lint")
    band = lambda iid, tags: PRI.kind_of(cfg, iid, tags)  # noqa: E731
    band_priority = lambda iid, tags: PRI.band(cfg, iid, tags)  # noqa: E731
    similar = _hunt(repo, log, cfg, st, set()) if len(scope) <= _DUPE_HUNT_MAX else None
    note = (
        ""
        if similar is not None
        else f"duplicates not checked: {len(scope)} scoped item(s) would each pay ~1s for "
        f"the add-time hunt; rerun --phase <id> or --since DATE to buy it back "
        f"(duplicates: 0 when skipped)"
    )
    report = PL.lint(snap, scope=scope, band=band, band_priority=band_priority, similar=similar)
    data: dict[str, Any] = {
        "clean": report.clean,
        **report.as_dict(),
        "note": note,
        "fixed": _apply_fixes(repo, agent, report) if fix else {},
    }
    if report.findings:
        return O.failed("plan.lint", _count_lint(report), **data)
    return O.ok("plan.lint", **data)
