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
from ..core import redact as R
from ..services.redact_report import redactor
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


def _envelope(cfg, kind: str, rid: str, source: str | None, provenance: dict | None) -> dict:
    data: dict[str, Any] = {"kind": kind, "id": rid}
    if source is not None:
        data["source"] = source
    data["provenance"] = {"by": cfg.agent.id, **(provenance or {})}
    return data


def _text(rid: str, fields: dict[str, Any], title: str, body: str) -> tuple[str, str]:
    """What the duplicate check compares: the caller's title and body, else the id and
    the fields' values (a definition is mostly its prose)."""
    if title or body:
        return title, body
    words = [str(v) for v in fields.values() if isinstance(v, str | int | float)]
    return rid, " ".join(words)


def def_record(
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
    agent: str = "",
) -> O.Outcome:
    """Record a WHOLE definition (`def.recorded`). Recording an id again replaces its
    fields and brings a retired, superseded or merged definition back."""
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
        log, cfg, kind, rid, fields, source, provenance, chk, replaced=prev is not None
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
    log, cfg, kind, rid, fields, source, provenance, chk: DD.Checked, *, replaced: bool
) -> O.Outcome:
    fields = _logged(cfg, fields)
    digest = D.digest(fields)
    data = _envelope(cfg, kind, rid, source, provenance)
    data.update(fields=dict(fields), digest=digest, **chk.fields)
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


def _logged(cfg, fields: dict[str, Any]) -> dict[str, Any]:
    """``fields`` as the committed log will hold them (the `log` redaction profile), so the
    digest is of what is stored: equal fields, equal digest (D-unify 6, 7)."""
    return R.redact_leaves(dict(fields), redactor("log", cfg))  # type: ignore[return-value]


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


def def_update(
    repo: Path,
    kind: str,
    rid: str,
    fields: dict[str, Any],
    *,
    source: str | None = None,
    provenance: dict[str, Any] | None = None,
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
    merged = _logged(cfg, {**rec.fields, **fields})
    merged = {k: v for k, v in merged.items() if not (k in fields and fields[k] is None)}
    digest = D.digest(merged)
    # None keeps the recorded provenance (under this author); a mapping replaces it.
    prov = (
        {k: v for k, v in rec.provenance.items() if k != "by"} if provenance is None else provenance
    )
    # Unnamed, it is no change of its own: an update that changes nothing else is exit 2
    # whoever asks. Written, `by` is the author of this revision.
    same_prov = provenance is None or {"by": cfg.agent.id, **prov} == rec.provenance
    if D.same(merged, rec.digest) and (source is None or source == rec.source) and same_prov:
        return O.nothing("def.updated", f"{kind} {rid}: nothing changed", def_kind=kind, id=rid)
    data = _envelope(cfg, kind, rid, rec.source if source is None else source, prov)
    data.update(fields=dict(fields), digest=digest)
    log.append("def.updated", D.key(kind, rid), data)
    changed = sorted(k for k in fields if rec.fields.get(k) != fields[k])
    return O.ok("def.updated", def_kind=kind, id=rid, digest=digest, changed=changed)


def def_retire(repo: Path, kind: str, rid: str, *, reason: str, agent: str = "") -> O.Outcome:
    """Retire an ACTIVE definition (`def.retired`): kept, with its history, but no longer
    in force. Needs a reason."""
    if not (reason or "").strip():
        return O.failed(
            "def.retired", "retiring a definition needs a reason", def_kind=kind, id=rid
        )
    log, cfg, st = _load(repo, agent)
    rec, refusal = _live(st, kind, rid, "def.retired")
    if refusal is not None:
        return refusal
    data = _envelope(cfg, kind, rid, rec.source, None)
    data.update(digest=rec.digest, reason=reason.strip())
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
    data = _envelope(cfg, kind, rid, rec.source, None)
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
