"""Fold handlers: sessions, CI results, and the ddflow-version events.

Each `_h_*` is a `(State, Event) -> None` fold handler, assembled into `model.HANDLERS`;
the other functions here are the helpers they share. All of it is pure."""

from __future__ import annotations

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


def _h_upgrade_applied(st: State, ev: Event) -> None:
    d = ev.data
    st.upgrades.append(
        {
            "from": d.get("from", ""),
            "to": d.get("to", ""),
            "categories": list(d.get("categories") or []),
            "backup": d.get("backup", ""),
            "agent": ev.agent,
            "at": ev.ts,
        }
    )


def _h_session_ended(st: State, ev: Event) -> None:
    sess = _session(st, ev)
    sess.ended_at = ev.ts
    sess.summary = (ev.data.get("summary") or "").strip() or sess.summary
