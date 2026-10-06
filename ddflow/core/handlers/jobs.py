"""Fold handlers: long-running jobs, external observations, schedules and triggers.

Assembled into `model.HANDLERS`; each is `(State, Event) -> None` and pure."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..events import Event
from ..records import Job, Schedule, State


def _h_job_started(st: State, ev: Event) -> None:
    d = ev.data
    st.jobs[ev.subject] = Job(
        id=ev.subject,
        item=d.get("item", ""),
        command=d.get("command", ""),
        pid=int(d.get("pid", 0) or 0),
        host=d.get("host", ""),
        proc_start=str(d.get("proc_start", "")),
        log=d.get("log", ""),
        cwd=d.get("cwd", ""),
        started_at=ev.ts,
        by=ev.agent,
    )


def _h_job_ended(st: State, ev: Event) -> None:
    j = st.jobs.get(ev.subject)
    if j is None:
        return
    j.ended_at = ev.ts
    code = ev.data.get("exit_code")
    j.exit_code = int(code) if code is not None else None
    j.note = ev.data.get("note", "") or j.note


def _h_external(st: State, ev: Event) -> None:
    st.external[ev.subject] = {
        "state": ev.data.get("state", ""),
        "title": ev.data.get("title", ""),
        "repo": ev.data.get("repo", ""),
        "at": ev.ts,
    }


#: What a `schedule.defined` / `schedule.updated` event may carry: every `Schedule` field
#: an author sets. `id` is the subject; `at`, `by` and `removed` are the fold's.
SCHEDULE_FIELDS: tuple[str, ...] = (
    "title",
    "cadence",
    "needs",
    "scope_globs",
    "concurrency_group",
    "prompt",
    "mode",
    "budget",
    "escalate",
    "missed",
    "jitter",
    "enabled",
    "tags",
)


def _schedule_with(base: Schedule, d: dict[str, Any]) -> Schedule:
    """`base` with the fields `d` carries; copied, so no list is shared with the event."""
    out = Schedule(**{**asdict(base)})
    for name in SCHEDULE_FIELDS:
        if name in d:
            v = d[name]
            setattr(
                out, name, list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v
            )
    return out


def _h_schedule_defined(st: State, ev: Event) -> None:
    """A whole definition: a field the event omits takes the model default, so defining a
    job again REPLACES it rather than merging onto the old one. Defining a removed job
    brings it back. When it was first defined, and by whom, is kept."""
    prev = st.schedules.get(ev.subject)
    job = _schedule_with(Schedule(id=ev.subject), ev.data)
    job.at = prev.at if prev and prev.at else ev.ts
    job.by = prev.by if prev and prev.by else ev.agent
    st.schedules[ev.subject] = job


def _h_schedule_updated(st: State, ev: Event) -> None:
    """Only the fields the event carries change. An update that arrives before its
    definition (a shard merged out of order) starts from the defaults rather than being
    dropped, and the definition then replaces it."""
    prev = st.schedules.get(ev.subject) or Schedule(id=ev.subject, at=ev.ts, by=ev.agent)
    st.schedules[ev.subject] = _schedule_with(prev, ev.data)


def _h_schedule_removed(st: State, ev: Event) -> None:
    job = st.schedules.get(ev.subject)
    if job is None:
        job = st.schedules[ev.subject] = Schedule(id=ev.subject, at=ev.ts, by=ev.agent)
    job.removed = ev.data.get("reason", "") or "removed"


TRIGGER_FIRES_KEPT = 200

TRIGGER_SUPPRESSIONS_KEPT = 50

TRIGGER_RUNS_KEPT = 20


def _h_trigger_fired(st: State, ev: Event) -> None:
    """One fire of a trigger and the items it filed (D-trigger-actions-create-items)."""
    d = ev.data
    fire = {
        "at": ev.ts,
        "key": str(d.get("key", "")),
        "items": [str(i) for i in d.get("items", [])],
        "hop": int(d.get("hop", 1) or 1),
        "digest": str(d.get("digest", "")),
        "job": str(d.get("job", "")),
        "events": list(d.get("events", [])),
    }
    tail = st.trigger_fires.setdefault(ev.subject, [])
    tail.append(fire)
    del tail[:-TRIGGER_FIRES_KEPT]
    # Only WHEN: the items are in `trigger_items`. One small entry per key ever fired,
    # and every fire filed a queue item, so this grows no faster than the queue itself.
    st.trigger_keys.setdefault(ev.subject, {})[fire["key"]] = {"at": fire["at"]}
    for item in fire["items"]:
        st.trigger_items[item] = {"trigger": ev.subject, "key": fire["key"], "hop": fire["hop"]}


def _h_trigger_suppressed(st: State, ev: Event) -> None:
    tail = st.trigger_suppressed.setdefault(ev.subject, [])
    tail.append(
        {
            "at": ev.ts,
            "key": str(ev.data.get("key", "")),
            "reason": str(ev.data.get("reason", "")),
            "detail": str(ev.data.get("detail", "")),
            "events": list(ev.data.get("events", [])),
        }
    )
    del tail[:-TRIGGER_SUPPRESSIONS_KEPT]


def _h_trigger_evaluated(st: State, ev: Event) -> None:
    st.trigger_runs.append(
        {
            "at": ev.ts,
            "by": ev.agent,
            **{k: ev.data.get(k) for k in ("now", "fired", "suppressed", "triggers", "errors")},
        }
    )
    del st.trigger_runs[:-TRIGGER_RUNS_KEPT]
