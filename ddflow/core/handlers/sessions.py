"""Fold handlers: sessions, CI results, and the ddflow-version events.

`model.HANDLERS` maps each event kind to an `_h_*` function here -- directly, or through
the small wrapper or factory call it shows. Everything in this module is pure."""

from __future__ import annotations

from typing import Any

from ..events import Event, version_key
from ..records import Session, State


def _session(st: State, ev: Event) -> Session:
    return st.sessions.setdefault(ev.subject, Session(id=ev.subject, agent=ev.agent))


def _h_session_started(st: State, ev: Event) -> None:
    """Merge, never replace: a reordered shard can deliver a prompt before its
    session.started, and replacing the object would drop an event that IS in the log."""
    s = _session(st, ev)
    s.model = ev.data.get("model", "") or s.model
    s.started_at = s.started_at or ev.ts


def _h_session_prompt(st: State, ev: Event) -> None:
    s = _session(st, ev)
    s.prompts.append(
        {
            "at": ev.ts,
            "text": ev.data.get("text", ""),
            "item": ev.data.get("item", ""),
            "seq": len(s.prompts),
        }
    )


CI_RESULTS_KEPT = 50


def _h_ci_result(st: State, ev: Event) -> None:
    """One outcome of the CI command: where it ran (gate | merge | pre-push | schedule),
    whether it held, which checks failed, and on what commit."""
    d = ev.data
    st.ci_results.append(
        {
            "at": ev.ts,
            "stage": d.get("stage", ""),
            "status": d.get("status", ""),
            "ok": bool(d.get("ok")),
            "sha": d.get("sha", ""),
            "subject": ev.subject,
            "checks": list(d.get("checks") or []),
        }
    )
    del st.ci_results[:-CI_RESULTS_KEPT]


def _h_session_note(st: State, ev: Event) -> None:
    """A note, with the fields that make it addressable afterwards.

    `seq`, `ident` and `source` used to be dropped here, which is how a projection
    quietly decides a field does not exist: the importer wrote `ident` on every note to
    make a second import idempotent, the fold discarded it, and the check that read it
    back compared `""` against `""` and reported "already imported" for nothing.
    """
    note = {
        "at": ev.ts,
        "text": ev.data.get("text", ""),
        "item": ev.data.get("item", ""),
    }
    # `seq` is assigned HERE, positionally, exactly as `_h_session_prompt` does -- the
    # caller's number is ignored. `apply_import` numbers from a fresh `enumerate` on
    # every run and writes into two fixed session ids, and `_h_session_started` merges
    # rather than replaces, so an incremental re-import appended notes 0,1,2 beside the
    # first run's 0,1,2. The index keys on `(session, 10_000 + seq)`, so each new note
    # silently overwrote an earlier one -- and `prompts_fts` then held two rows under
    # one doc id, so a query matching the OLD text resolved to the surviving row and
    # returned text not containing the query terms. One authority for the number.
    sess = _session(st, ev)
    note["seq"] = len(sess.notes)
    for key in ("ident", "source"):
        if ev.data.get(key) not in (None, ""):
            note[key] = ev.data[key]
    # When the note records something that happened BEFORE it was written down -- an
    # imported journal entry, a memory dated months ago -- keep both: `at` is when
    # ddflow learned it, `origin_at` is when it was true.
    if ev.data.get("at"):
        note["origin_at"] = ev.data["at"]
    sess.notes.append(note)


def _h_ddflow_seen(st: State, ev: Event) -> None:
    v = ev.data.get("version")
    if not isinstance(v, str) or not version_key(v):
        return
    rec = st.ddflow_versions.setdefault(
        v, {"agents": [], "at": ev.ts, "install": str(ev.data.get("install", ""))}
    )
    if ev.agent not in rec["agents"]:
        rec["agents"].append(ev.agent)
    rec["at"] = min(rec["at"], ev.ts)


def _h_ddflow_capabilities(st: State, ev: Event) -> None:
    """A capability the log's data needs (`ddflow.capabilities`): the kinds union and the
    highest release, folded the same way the write guard does (`events._fold_capability`)."""
    name, kinds, v = ev.data.get("capability"), ev.data.get("kinds"), ev.data.get("version")
    if not (isinstance(name, str) and name and isinstance(kinds, list) and isinstance(v, str)):
        return
    rec = st.capabilities.setdefault(name, {"kinds": [], "version": v, "agents": [], "at": ev.ts})
    rec["kinds"] = sorted({*rec["kinds"], *(k for k in kinds if isinstance(k, str))})
    if (version_key(v), v) > (version_key(rec["version"]), rec["version"]):
        rec["version"] = v
    if ev.agent not in rec["agents"]:
        rec["agents"].append(ev.agent)
    rec["at"] = min(rec["at"], ev.ts)


def _h_skew_overridden(st: State, ev: Event) -> None:
    d = ev.data
    st.skew_overrides.append(
        {
            "agent": ev.agent,
            "session": d.get("session", ""),
            "running": d.get("running", ""),
            "log_version": d.get("log_version", ""),
            "reason": d.get("reason", ""),
            "at": ev.ts,
        }
    )


def _upgrade_run(st: State, run_id: str, ev: Event) -> dict[str, Any]:
    """The run record for ``run_id`` in ``st.upgrade_runs``, created by its first event, and
    updated by this one as its ``status`` says (``started``, ``step``, else a closing event)."""
    d, status = ev.data, ev.data.get("status", "done")
    run = st.upgrade_runs.setdefault(
        run_id,
        {
            "from": "",
            "to": "",
            "categories": [],
            "items": [],
            "steps": [],
            "backup": "",
            "confirmed": {},
            "closed": "",
            "started": ev.ts,
            "agent": ev.agent,
            "starts": 0,
        },
    )
    if status == "started":
        run["starts"] += 1
        run["closed"] = ""  # a run is open again once it is started again
        # The FIRST start names the plan; a resume's start repeats only what was left of it.
        # `source` and `target`, not `from` and `to`: an older ddflow folds this event as a
        # finished upgrade and must not read the version a run is only heading for as reached.
        if run["starts"] == 1:
            run["from"], run["to"] = d.get("source", ""), d.get("target", "")
            run["categories"] = list(d.get("categories") or [])
            run["items"] = [i for i in d.get("items") or [] if isinstance(i, str)]
        run["backup"] = d.get("backup", "") or run["backup"]
        if isinstance(d.get("confirmed"), dict):  # what the operator accepted, kept across resumes
            run["confirmed"] = {**run["confirmed"], **d["confirmed"]}
    elif status == "step":
        run["steps"].append(
            {
                "id": str(d.get("step", "")),
                "status": str(d.get("step_status", "")),
                "detail": str(d.get("detail", "")),
                "at": ev.ts,
            }
        )
    else:
        run["closed"] = "failed" if status == "failed" else "done"
        run["summary"] = str(d.get("summary", ""))
    # What the run wrote, by digest (the closing event, done or failed): the commit hook waves
    # exactly these bytes through without a lease (services.upgrade_written).
    if isinstance(d.get("written"), dict):
        run.setdefault("written", {}).update(
            {k: v for k, v in d["written"].items() if isinstance(k, str) and isinstance(v, str)}
        )
    return run


def _h_upgrade_applied(st: State, ev: Event) -> None:
    """An upgrade's record. An event without ``status`` (or ``done``) is a finished upgrade, as
    ever; ``started``, ``step`` and ``failed`` (with a ``run`` id) are the run's progress, kept in
    ``st.upgrade_runs`` so an interrupted run can be resumed and shown."""
    d = ev.data
    run_id = d.get("run") if isinstance(d.get("run"), str) else ""
    if run_id:
        _upgrade_run(st, run_id, ev)
    if d.get("status", "done") not in ("done", None):
        return
    st.upgrades.append(
        {
            "from": d.get("from", ""),
            "to": d.get("to", ""),
            "categories": list(d.get("categories") or []),
            "backup": d.get("backup", ""),
            "items": [i for i in d.get("items") or [] if isinstance(i, str)],
            "confirmed": dict(d["confirmed"]) if isinstance(d.get("confirmed"), dict) else {},
            "config_changes": [c for c in d.get("config_changes") or [] if isinstance(c, dict)],
            "summary": str(d.get("summary", "")),
            "run": run_id,
            "agent": ev.agent,
            "at": ev.ts,
        }
    )


def _h_repair_applied(st: State, ev: Event) -> None:
    d = ev.data
    rid = d.get("repair") or ev.subject
    if not isinstance(rid, str) or not rid:
        return
    keys = d.get("findings")
    st.repairs_applied.setdefault(rid, []).append(
        {
            "findings": [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else [],
            "since": str(d.get("since", "")),
            "version": str(d.get("version", "")),
            "events": d.get("events", 0) if isinstance(d.get("events"), int) else 0,
            "agent": ev.agent,
            "at": ev.ts,
        }
    )


def _h_session_ended(st: State, ev: Event) -> None:
    sess = _session(st, ev)
    sess.ended_at = ev.ts
    sess.summary = (ev.data.get("summary") or "").strip() or sess.summary
