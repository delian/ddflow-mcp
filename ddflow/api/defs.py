"""The one write path for managed definitions (`core.defs`): record, update, retire,
supersede, merge -- and read them back.

Every family of definitions (doc types, schedules, triggers, skills, agents, research
claims, rules) writes through these instead of growing its own events, fold and revise
path (R-unify, B-uni-def-records). Each validates first and writes nothing when the
request is wrong:

* a kind not in `core.defs.DEF_KINDS`, an id that cannot be an id, fields that are not a
  JSON object -- ``failed`` (exit 1);
* revising a definition that does not exist or is no longer active, superseding or
  merging into one that is not active, or across kinds -- ``refused`` (exit 3);
* an update that changes nothing -- ``nothing`` (exit 2).

A NEW definition runs the add-time duplicate check (`api._dedupe.check_add`) like every
other add, under its own kind's name: it runs when that kind is in ``[dedupe].kinds``
(``rule`` is by default; a family adds its kind when it starts filing). Recording an
existing id again replaces it, as defining a schedule again does, and is not re-checked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ..config import id_problem
from ..core import defs as D
from ..core import outcome as O
from ..services import sessions as SE
from ..services.guidance import deffields as DF
from ._base import _load


def _bad(kind: str, rid: str) -> str:
    if kind not in D.DEF_KINDS:
        return f"unknown definition kind {kind!r}; the kinds are {', '.join(D.DEF_KINDS)}"
    if problem := id_problem(rid):
        return f"definition id {rid!r} {problem}"
    return ""


def _fields_problem(fields: Any, *, nulls: bool = False) -> str:
    """Why ``fields`` cannot be stored, or "". A null removes a field in an update, so a
    whole definition holds none (``nulls`` is the update's allowance)."""
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return "fields must be a JSON object (string keys)"
    if not nulls and (empty := sorted(k for k, v in fields.items() if v is None)):
        return f"field(s) {', '.join(map(str, empty))} are null: leave them out instead"
    try:
        json.dumps(fields, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return f"fields must be plain JSON: {exc}"
    return ""


#: Provenance keys the writer stamps itself on every write (RC3): not part of what a caller
#: asks to keep, and never carried over from an earlier revision.
#: ... of which `session` and `prompt` are never the caller's to name (`by` may be, by an import).
SELF_STAMPED = ("session", "prompt")
STAMPED = ("by", *SELF_STAMPED)


def evidence(log) -> dict[str, str]:
    """Where a write came from (RC3): ``session`` is the writing agent's open session (the one
    most recently active) and ``prompt`` the id of that session's latest operator prompt,
    each only when there is one. A write never opens a session of its own."""
    events = log.read_all()
    live = SE.open_sessions(events, log.agent_id)
    if not live:
        return {}
    out = {"session": live[0]}
    prompts = [e for e in events if e.kind == "session.prompt" and e.subject == live[0]]
    if prompts:
        out["prompt"] = max(prompts, key=lambda e: (e.lamport, e.ts)).id
    return out


def _envelope(
    cfg, kind: str, rid: str, source: str | None, provenance: dict | None, log=None
) -> dict:
    data: dict[str, Any] = {"kind": kind, "id": rid}
    if source is not None:
        data["source"] = source
    # session and prompt are THIS write's: a carried-over provenance never brings an older
    # revision's along (a caller that names `by` keeps it: an import says who authored the file)
    carried = {k: v for k, v in (provenance or {}).items() if k not in SELF_STAMPED}
    stamp = evidence(log) if log is not None else {}
    data["provenance"] = {"by": cfg.agent.id, **carried, **stamp}
    return data


def _text(rid: str, fields: dict[str, Any], title: str, body: str) -> tuple[str, str]:
    """What the duplicate check compares: the caller's title and body, else the id and
    the fields' values (a definition is mostly its prose)."""
    if title or body:
        return title, body
    words = [str(v) for v in fields.values() if isinstance(v, str | int | float)]
    return rid, " ".join(words)


def def_record(  # noqa: PLR0913 -- one keyword per facet of a definition, all keyword-only
    repo: Path,
    kind: str,
    rid: str,
    fields: dict[str, Any],
    *,
    source: str = "",
    provenance: dict[str, Any] | None = None,
    title: str = "",
    body: str = "",
    answer: DD.Answer | None = None,
    reason: str = "",
    agent: str = "",
) -> O.Outcome:
    """Record a WHOLE definition (`def.recorded`). Recording an id again replaces its
    fields and brings a retired, superseded or merged definition back. ``reason`` is kept
    on the event (a restore says why)."""
    if bad := _bad(kind, rid) or _fields_problem(fields):
        return O.failed("def.recorded", bad, def_kind=kind, id=rid)
    log, cfg, st = _load(repo, agent)
    prev = st.defs.get(D.key(kind, rid))
    chk = DD.Checked()
    if prev is None:
        title, body = _text(rid, fields, title, body)
        rec = DD.Record(
            kind=kind, event_kind="def.recorded", rid=D.key(kind, rid), title=title, body=body
        )
        chk = DD.check_add(repo, log, cfg, st, rec, answer)
        if chk.refusal is not None:
            return chk.refusal
        if chk.extension:
            return DD.extend(log, cfg, chk, "def.recorded")
    return _write_record(
        log,
        cfg,
        kind,
        rid,
        fields,
        source,
        provenance,
        chk,
        replaced=prev is not None,
        reason=reason,
    )


def def_record_unchecked(
    repo: Path,
    kind: str,
    rid: str,
    fields: dict[str, Any],
    *,
    source: str = "",
    provenance: dict[str, Any] | None = None,
    agent: str = "",
) -> O.Outcome:
    """`def_record` without the add-time duplicate check, for a writer that has run its own
    (a rule add checks against rules AND every other kind, with the adder's answer)."""
    if bad := _bad(kind, rid) or _fields_problem(fields):
        return O.failed("def.recorded", bad, def_kind=kind, id=rid)
    log, cfg, st = _load(repo, agent)
    prev = st.defs.get(D.key(kind, rid))
    return _write_record(
        log, cfg, kind, rid, fields, source, provenance, DD.Checked(), replaced=prev is not None
    )


def _write_record(
    log,
    cfg,
    kind,
    rid,
    fields,
    source,
    provenance,
    chk: DD.Checked,
    *,
    replaced: bool,
    reason: str = "",
) -> O.Outcome:
    fields = DF.logged(fields, cfg)
    digest = D.digest(fields)
    data = _envelope(cfg, kind, rid, source, provenance, log)
    data.update(fields=dict(fields), digest=digest, **chk.fields)
    if reason.strip():
        data["reason"] = reason.strip()
    with log.transaction():
        log.append("def.recorded", D.key(kind, rid), data)
        DD.after_add(log, cfg, D.key(kind, rid), chk)
    return O.ok(
        "def.recorded",
        def_kind=kind,
        id=rid,
        digest=digest,
        replaced=replaced,
        **chk.data(),
    )


def _live(st, kind: str, rid: str, event_kind: str) -> tuple[Any, O.Outcome | None]:
    if bad := _bad(kind, rid):
        return None, O.failed(event_kind, bad, def_kind=kind, id=rid)
    rec = st.defs.get(D.key(kind, rid))
    if rec is None:
        return None, O.refused(event_kind, f"no {kind} definition {rid!r}", def_kind=kind, id=rid)
    if not rec.live:
        after = f" by {rec.successor}" if rec.successor else ""
        return None, O.refused(
            event_kind,
            f"{kind} {rid!r} is {rec.status}{after}; record it again to bring it back",
            def_kind=kind,
            id=rid,
        )
    return rec, None


def _core(prov: dict[str, Any]) -> dict[str, Any]:
    """``prov`` without what the writer stamps itself (`STAMPED`)."""
    return {k: v for k, v in prov.items() if k not in STAMPED}


def _update_data(
    log, cfg, rec, fields: dict[str, Any], source: str | None, provenance: dict | None, reason: str
) -> dict[str, Any] | None:
    """The `def.updated` payload that applies ``fields`` to ``rec``, or None when the result
    is what is already recorded."""
    merged = DF.logged({**rec.fields, **fields}, cfg)
    merged = {k: v for k, v in merged.items() if not (k in fields and fields[k] is None)}
    digest = D.digest(merged)
    # None keeps the recorded provenance (under this author); a mapping replaces it.
    prov = _core(rec.provenance) if provenance is None else provenance
    # Unnamed, it is no change of its own: an update that changes nothing else is exit 2
    # whoever asks. Written, `by` is the author of this revision.
    unstamped = {k: v for k, v in rec.provenance.items() if k not in SELF_STAMPED}
    named = {k: v for k, v in prov.items() if k not in SELF_STAMPED}
    same_prov = provenance is None or {"by": cfg.agent.id, **named} == unstamped
    if D.same(merged, rec.digest) and (source is None or source == rec.source) and same_prov:
        return None
    data = _envelope(cfg, rec.kind, rec.id, rec.source if source is None else source, prov, log)
    data.update(fields=dict(fields), digest=digest)
    if reason.strip():
        data["reason"] = reason.strip()
    return data


def def_update(
    repo: Path,
    kind: str,
    rid: str,
    fields: dict[str, Any],
    *,
    source: str | None = None,
    provenance: dict[str, Any] | None = None,
    reason: str = "",
    agent: str = "",
) -> O.Outcome:
    """Change some fields of an ACTIVE definition (`def.updated`); a field set to None is
    removed. Exit 2 when the result is what is already recorded."""
    if bad := _fields_problem(fields, nulls=True):
        return O.failed("def.updated", bad, def_kind=kind, id=rid)
    log, cfg, st = _load(repo, agent)
    rec, refusal = _live(st, kind, rid, "def.updated")
    if refusal is not None:
        return refusal
    data = _update_data(log, cfg, rec, fields, source, provenance, reason)
    if data is None:
        return O.nothing("def.updated", f"{kind} {rid}: nothing changed", def_kind=kind, id=rid)
    log.append("def.updated", D.key(kind, rid), data)
    changed = sorted(k for k in fields if rec.fields.get(k) != fields[k])
    return O.ok("def.updated", def_kind=kind, id=rid, digest=data["digest"], changed=changed)


def def_retire(
    repo: Path,
    kind: str,
    rid: str,
    *,
    reason: str,
    split_into: list[str] | None = None,
    agent: str = "",
) -> O.Outcome:
    """Retire an ACTIVE definition (`def.retired`): kept, with its history, but no longer
    in force. Needs a reason. ``split_into`` names the parts a split replaced it with."""
    if not (reason or "").strip():
        return O.failed(
            "def.retired", "retiring a definition needs a reason", def_kind=kind, id=rid
        )
    log, cfg, st = _load(repo, agent)
    rec, refusal = _live(st, kind, rid, "def.retired")
    if refusal is not None:
        return refusal
    data = _envelope(cfg, kind, rid, rec.source, None, log)
    data.update(digest=rec.digest, reason=reason.strip())
    if split_into:
        data["split_into"] = list(split_into)
    log.append("def.retired", D.key(kind, rid), data)
    return O.ok("def.retired", def_kind=kind, id=rid)


def _replace(
    repo: Path, event_kind: str, kind: str, rid: str, by: str, reason: str, agent: str
) -> O.Outcome:
    """`supersede` and `merge`: ``rid`` hands over to ``by``, another ACTIVE definition of
    the same kind."""
    if rid == by:
        return O.failed(event_kind, f"{kind} {rid!r} cannot replace itself", def_kind=kind, id=rid)
    log, cfg, st = _load(repo, agent)
    rec, refusal = _live(st, kind, rid, event_kind)
    if refusal is None:
        _succ, refusal = _live(st, kind, by, event_kind)
    if refusal is not None:
        return refusal
    data = _envelope(cfg, kind, rid, rec.source, None, log)
    data.update(digest=rec.digest, successor=by)
    if reason.strip():
        data["reason"] = reason.strip()
    log.append(event_kind, D.key(kind, rid), data)
    return O.ok(event_kind, def_kind=kind, id=rid, successor=by)


def def_supersede(
    repo: Path, kind: str, rid: str, by: str, *, reason: str = "", agent: str = ""
) -> O.Outcome:
    """``rid`` is replaced by ``by`` (`def.superseded`); both stay, ``rid`` names it."""
    return _replace(repo, "def.superseded", kind, rid, by, reason, agent)


def def_merge(
    repo: Path, kind: str, rid: str, into: str, *, reason: str = "", agent: str = ""
) -> O.Outcome:
    """``rid`` is folded into ``into`` (`def.merged`): ``into`` lists it as merged from.
    Moving ``rid``'s content is the caller's: update ``into`` first."""
    return _replace(repo, "def.merged", kind, rid, into, reason, agent)


def _row(rec) -> dict[str, Any]:
    return {
        "def_kind": rec.kind,
        "id": rec.id,
        "status": rec.status,
        "digest": rec.digest,
        "source": rec.source,
        "successor": rec.successor,
        "at": rec.at,
        "by": rec.by,
        "updated_at": rec.updated_at,
    }


def def_show(repo: Path, kind: str, rid: str, *, agent: str = "") -> O.Outcome:
    """One definition: its fields, envelope, status and history."""
    _log, _cfg, st = _load(repo, agent)
    rec = st.defs.get(D.key(kind, rid))
    if rec is None:
        return O.failed("def.show", f"no {kind} definition {rid!r}", def_kind=kind, id=rid)
    return O.ok(
        "def.show",
        **_row(rec),
        fields=rec.fields,
        provenance=rec.provenance,
        status_reason=rec.reason,
        merged_from=rec.merged_from,
        history=rec.history,
    )


def def_list(
    repo: Path, *, kind: str = "", include_inactive: bool = False, agent: str = ""
) -> O.Outcome:
    """Definitions, by kind then id: the active ones, or every one."""
    _log, _cfg, st = _load(repo, agent)
    rows = [
        _row(r)
        for _k, r in sorted(st.defs.items())
        if (not kind or r.kind == kind) and (include_inactive or r.live)
    ]
    if not rows:
        return O.nothing("def.list", "no definitions", rows=[], count=0)
    return O.ok("def.list", rows=rows, count=len(rows))
