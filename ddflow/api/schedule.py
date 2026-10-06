"""Scheduled jobs and event triggers (D-sched-no-daemon, D-sched-triggers-separate).

`schedule_list` / `schedule_show` / `schedule_search` read the merge of the event log,
`.ddflow/schedules/*.toml` and `[cadence]` (`services.schedule.definitions`).

`schedule_define` / `schedule_update` / `schedule_remove` are the WRITERS of the
`schedule.*` events: each validates first and writes nothing when the definition is
wrong -- a bad field, a needs naming no job, a needs cycle. They are the layer the
authoring surfaces (duplicate check, enable/disable, MCP) are built on.

The graph checks read the log, then append: two writers racing can each pass and
together close a cycle (or remove a job the other just made a need). Nothing is lost --
`definitions` reports the cycle on every read -- but the refusal is best-effort, not a
lock; the last writer's definition stands.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.model import SCHEDULE_FIELDS, Schedule
from ..services import schedule as SV
from ..services import triggers as TR
from ._base import _load


def _defs(repo: Path, agent: str = ""):
    log, cfg, st = _load(repo, agent)
    return log, cfg, st, SV.definitions(repo, cfg, st)


def schedule_list(
    repo: Path, *, tag: str = "", enabled_only: bool = False, agent: str = ""
) -> O.Outcome:
    """Every job, by id, with where it is defined. Exit 2 when there is none; the
    definition problems found on the way are carried, never hidden."""
    _log, _cfg, _st, defs = _defs(repo, agent)
    rows = [
        d.row()
        for _jid, d in sorted(defs.jobs.items())
        if (not tag or tag in d.job.tags) and (d.job.enabled or not enabled_only)
    ]
    if not rows:
        return O.nothing("schedule.list", "no scheduled jobs", rows=[], count=0, errors=defs.errors)
    return O.ok("schedule.list", rows=rows, count=len(rows), errors=defs.errors)


def schedule_show(repo: Path, jid: str, *, agent: str = "") -> O.Outcome:
    """One job: its definition and source, what it needs and what needs it, the enabled
    jobs it may not run beside, and its runs (`cadence.ran` under its id)."""
    _log, cfg, st, defs = _defs(repo, agent)
    d = defs.jobs.get(jid)
    if d is None:
        gone = st.schedules.get(jid)
        why = f" (removed: {gone.removed})" if gone is not None and gone.removed else ""
        return O.failed("schedule.show", f"no such job {jid!r}{why}", id=jid)
    return O.ok(
        "schedule.show",
        **d.row(),
        needed_by=sorted(i for i, o in defs.jobs.items() if jid in o.job.needs),
        conflicts=SV.conflicts_of(defs, jid, cfg),
        runs=list(st.cadences.get(jid, [])),
        errors=[e for e in defs.errors if SV.concerns(e, jid)],
    )


def schedule_search(repo: Path, query: str, *, agent: str = "") -> O.Outcome:
    """Jobs matching every word of `query` (id, title, tags, prompt, needs, scope, group)."""
    _log, _cfg, _st, defs = _defs(repo, agent)
    rows = [d.row() for d in SV.search(defs, query)]
    if not rows:
        return O.nothing(
            "schedule.search",
            f"no job matches {query!r}",
            rows=[],
            count=0,
            query=query,
            errors=defs.errors,
        )
    return O.ok("schedule.search", rows=rows, count=len(rows), query=query, errors=defs.errors)


def _graph_refusal(kind: str, jid: str, defs, job) -> O.Outcome | None:
    """The graph problems `job` brings with it: a needs of its own naming no job, or a
    cycle through it -- refused even when the definition it replaces had the same
    problem. Problems of OTHER jobs that were there before it are theirs, not refused here."""
    jobs = {i: d.job for i, d in defs.jobs.items()}
    before = set(SV.graph_errors(jobs))
    jobs[jid] = job
    after = [e for e in SV.graph_errors(jobs) if e not in before or SV.concerns(e, jid)]
    if after:
        return O.failed(kind, f"{jid}: " + "; ".join(after), id=jid, errors=after)
    return None


def schedule_define(repo: Path, jid: str, spec: dict[str, Any], *, agent: str = "") -> O.Outcome:
    """Record a WHOLE job definition (`schedule.defined`). A field `spec` leaves out takes
    its default, so defining an existing id replaces it; a removed id comes back."""
    job, errors = SV.build(jid, dict(spec))
    if job is None:
        return O.failed("schedule.defined", f"{jid}: " + "; ".join(errors), id=jid, errors=errors)
    log, _cfg, _st, defs = _defs(repo, agent)
    refusal = _graph_refusal("schedule.defined", jid, defs, job)
    if refusal is not None:
        return refusal
    data = {name: getattr(job, name) for name in SCHEDULE_FIELDS if name in spec}
    log.append("schedule.defined", jid, data)
    shadowed = defs.jobs.get(jid)
    return O.ok(
        "schedule.defined",
        id=jid,
        replaced=bool(shadowed and shadowed.source == SV.SOURCE_LOG),
        shadows=[shadowed.source] if shadowed and shadowed.source != SV.SOURCE_LOG else [],
    )


def schedule_update(repo: Path, jid: str, fields: dict[str, Any], *, agent: str = "") -> O.Outcome:
    """Change some fields of a RECORDED job (`schedule.updated`). A job defined only by a
    file or by `[cadence]` is changed where it is defined, not here."""
    log, _cfg, st, defs = _defs(repo, agent)
    cur = st.schedules.get(jid)
    if cur is None or cur.removed:
        d = defs.jobs.get(jid)
        where = f" -- it is defined by {d.source}; change it there" if d is not None else ""
        return O.failed("schedule.updated", f"no recorded job {jid!r}{where}", id=jid)
    clean, errors = SV.normalize(dict(fields), partial=True)
    if not errors and jid in clean.get("needs", []):
        errors.append(f"{jid} cannot need itself")
    if errors:
        return O.failed("schedule.updated", f"{jid}: " + "; ".join(errors), id=jid, errors=errors)
    changed = {k: v for k, v in clean.items() if getattr(cur, k) != v}
    if not changed:
        return O.nothing("schedule.updated", f"{jid}: nothing changed", id=jid, changed=[])
    job = Schedule(**{**asdict(cur), **changed})
    refusal = _graph_refusal("schedule.updated", jid, defs, job)
    if refusal is not None:
        return refusal
    log.append("schedule.updated", jid, changed)
    return O.ok("schedule.updated", id=jid, changed=sorted(changed))


def schedule_remove(repo: Path, jid: str, *, reason: str, agent: str = "") -> O.Outcome:
    """Remove a recorded job (`schedule.removed`), with the reason. Kept in the log; it
    also hides a file or `[cadence]` job of the same id. Refused while another job needs it."""
    if not (reason or "").strip():
        return O.failed("schedule.removed", "a removal needs --reason", id=jid)
    log, _cfg, st, defs = _defs(repo, agent)
    cur = st.schedules.get(jid)
    if cur is None or cur.removed:
        return O.failed("schedule.removed", f"no recorded job {jid!r}", id=jid)
    users = sorted(i for i, d in defs.jobs.items() if jid in d.job.needs and i != jid)
    if users:
        return O.refused(
            "schedule.removed",
            f"{', '.join(users)} need {jid}: remove or change them first",
            id=jid,
            needed_by=users,
        )
    log.append("schedule.removed", jid, {"reason": reason.strip()})
    return O.ok("schedule.removed", id=jid, why=reason.strip())


# -- triggers (D-sched-triggers-separate, D-trigger-actions-create-items) --------------------


def _triggers(repo: Path, agent: str = ""):
    log, cfg, st, defs = _defs(repo, agent)
    trigs, errors = TR.load(repo, defs.jobs)
    return log, cfg, st, defs, trigs, errors


def _trigger_row(st, t) -> dict[str, Any]:
    fires = st.trigger_fires.get(t.id, [])
    return {
        **asdict(t),
        "fires": len(fires),
        "open": sorted(
            i
            for f in st.trigger_keys.get(t.id, {}).values()
            for i in f.get("items", [])
            if TR.outcome(st, i) == "open"
        ),
        "held": TR._held(st, t) >= t.breaker,
    }


def trigger_list(repo: Path, *, agent: str = "") -> O.Outcome:
    """Every trigger defined in `.ddflow/triggers/`, by id, and the files that are broken."""
    _log, _cfg, st, _defs, trigs, errors = _triggers(repo, agent)
    rows = [_trigger_row(st, t) for _tid, t in sorted(trigs.items())]
    if not rows:
        return O.nothing("trigger.list", "no triggers", rows=[], count=0, errors=errors)
    return O.ok("trigger.list", rows=rows, count=len(rows), errors=errors)


def trigger_show(repo: Path, tid: str, *, agent: str = "") -> O.Outcome:
    """One trigger: its definition, fires, open remediations, whether its breaker holds,
    and the last suppressions."""
    _log, _cfg, st, _defs, trigs, errors = _triggers(repo, agent)
    t = trigs.get(tid)
    if t is None:
        why = "; ".join(e for e in errors if f"/{tid}.toml" in e)
        return O.failed(
            "trigger.show", f"no such trigger {tid!r}" + (f": {why}" if why else ""), id=tid
        )
    return O.ok(
        "trigger.show",
        **_trigger_row(st, t),
        history=list(st.trigger_fires.get(tid, [])),
        suppressed=list(st.trigger_suppressed.get(tid, []))[-TR.SHOWN_SUPPRESSIONS :],
    )


def trigger_evaluate(
    repo: Path, *, now: str = "", dry_run: bool = False, agent: str = ""
) -> O.Outcome:
    """Evaluate every trigger against the log NOW and act: a met condition files its
    remediation items (`trigger.fired` + `task.added`) or is recorded as suppressed with
    the reason; the run itself is `trigger.evaluated`. `dry_run` writes nothing. Exit 2
    when no condition is met (nothing recorded but the run).

    The read, the decision and the writes happen under ONE log lock: two evaluators
    started at once would otherwise both see a key with no open remediation, or the
    hourly cap not yet reached, and both file (rubber-duck and roborev on B-trigger-model)."""
    from ..core.model import fold

    log, cfg, _st = _load(repo, agent)
    at = TR.ts(now) if now else datetime.now(UTC)
    if at is None:
        return O.failed("trigger.evaluated", f"--now {now!r} is not an ISO timestamp")
    with log.transaction():
        events = log.read_all()
        st = fold(events)
        defs = SV.definitions(repo, cfg, st)
        trigs, errors = TR.load(repo, defs.jobs)
        decisions = TR.evaluate(st, events, trigs, at)
        if not dry_run:
            _apply(log, st, defs, trigs, decisions, at, errors)
    fired = sum(1 for d in decisions if d.fire)
    data = {"decisions": [asdict(d) for d in decisions], "errors": errors, "fired": fired}
    if dry_run:
        data["dry_run"] = True
    if not decisions:
        return O.nothing("trigger.evaluated", "no trigger condition is met", **data)
    return O.ok("trigger.evaluated", **data)


def _apply(log, st, defs, trigs, decisions, at, errors) -> None:
    """Write what `decisions` decided: each fire's item and `trigger.fired`, each
    suppression, then the run. The caller holds the log lock."""
    taken = set(st.items)
    for d in decisions:
        if not d.fire:
            log.append(
                "trigger.suppressed",
                d.trigger,
                {"key": d.key, "reason": d.reason, "detail": d.detail, "events": d.events},
            )
            continue
        trig = trigs[d.trigger]
        item = TR.item_for(trig, defs.jobs[trig.action["job"]].job, d, taken, st)
        taken.add(item["id"])
        log.append("task.added", item["id"], item["data"])
        log.append(
            "trigger.fired",
            d.trigger,
            {
                "key": d.key,
                "items": [item["id"]],
                "hop": d.hop,
                "digest": trig.digest(),
                "job": trig.action["job"],
                "events": d.events,
            },
        )
        d.items = [item["id"]]
    log.append(
        "trigger.evaluated",
        "triggers",
        {
            "now": at.isoformat(),
            "triggers": sorted(trigs),
            "fired": sum(1 for d in decisions if d.fire),
            "suppressed": sum(1 for d in decisions if not d.fire),
            "errors": errors,
        },
    )
