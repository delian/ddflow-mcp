"""`ddflow update` on every record kind that is not a work item (DX-update-universal).

`items.update` edits a task or phase; given any other id it comes here. The record's kind is
found from its id (``bug:B1`` names it when two kinds share one), the edit is checked against the
catalogue (`core.record_fields`) -- one answer to "which operations does this kind take" for
every surface -- and written by the kind's own writer: `<kind>.updated` for the records that
are events of their own kind, `rule_update`, `schedule_update` or `def_update` for the rest.
Nothing here rewrites an earlier event: an append is resolved against the text current when the
edit is recorded and the new text is what the event carries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import globspec as GS
from ..core import outcome as O
from ..core import record_fields as RF
from ..core.defs import key as def_key
from ._base import _load

_EV = "record.updated"


def _fail(kind: str, rid: str, msg: str) -> O.Outcome:
    return O.failed(_EV, msg, id=rid, record_kind=kind)


def data_fields(kind) -> RF.KindFields:
    """The catalogue entry of a data-declared kind (`api.records.RecordKind`), derived from its
    declaration: title, the first prose field as the body, tags and globs where it has them."""
    names = {f.name: f for f in kind.fields}
    body = next(
        (t for t in kind.text if t != "tags" and names.get(t, None) and names[t].type == "string"),
        "",
    )
    attrs = {"title": kind.title}
    if body:
        attrs["body"] = body
    for op in ("tags", "globs", "priority"):
        if op in names:
            attrs[op] = op
    used = set(attrs.values())
    kinds = {"string": RF.STRING, "array": RF.LIST, "integer": RF.INT, "boolean": RF.BOOL}
    own = tuple(
        RF.Own(f.name, kinds.get(f.type, RF.STRING)) for f in kind.fields if f.name not in used
    )
    return RF.KindFields(
        kind.name, RF.DATA, "", attrs, own, nearest=f"ddflow record {kind.name} revise"
    )


def _data_kind(name: str):
    from . import records as R

    k = R.KINDS.get(name)
    return k if k is not None and k.name != "rule" else None


def _candidates(st, rid: str) -> list[str]:
    """The kinds that have a record called ``rid`` (not work items: the caller handled those)."""
    got = []
    for kind, book in (
        ("bug", st.bugs),
        ("lesson", st.lessons),
        ("research", st.research),
        ("decision", st.decisions),
        ("memory", st.memories),
        ("session", st.sessions),
    ):
        if rid in book:
            got.append(kind)
    job = st.schedules.get(rid)
    if job is not None and not job.removed:
        got.append("schedule")
    for rec in st.defs.values():
        if rec.id == rid and rec.live and rec.kind not in got:
            got.append(rec.kind)
    return got


def resolve(st, ref: str) -> tuple[str, str, str]:
    """(kind, id, problem) for ``ref``: ``kind:id`` or a bare id. ``problem`` is non-empty when
    the id names no record or several."""
    if ":" in ref:
        kind, _, rid = ref.partition(":")
        if kind in RF.ALL_KIND_NAMES or _data_kind(kind) is not None or kind == "rule":
            return kind, rid, ""
    found = _candidates(st, ref)
    if not found:
        return "", ref, f"no such record {ref!r}"
    if len(found) > 1:
        forms = ", ".join(f"{k}:{ref}" for k in found)
        return "", ref, f"{ref!r} names several records ({', '.join(found)}): say which, as {forms}"
    return found[0], ref, ""


def _entry(kind: str) -> RF.KindFields | None:
    if kind in RF.KINDS:
        return RF.KINDS[kind]
    dk = _data_kind(kind)
    return data_fields(dk) if dk is not None else None


def _current(repo: Path, st, kind: str, rid: str, kd: RF.KindFields) -> dict[str, Any] | O.Outcome:
    """The record's present values, by the catalogue's field names."""
    if kd.store == RF.EVENT:
        book = {
            "bug": st.bugs,
            "lesson": st.lessons,
            "research": st.research,
            "decision": st.decisions,
            "memory": st.memories,
            "session": st.sessions,
        }[kind]
        rec = book.get(rid)
        if rec is None:
            return _fail(kind, rid, f"no {kind} {rid!r}")
        names = {*kd.attrs.values(), *(o.name for o in kd.own)}
        return {n: getattr(rec, n) for n in names if hasattr(rec, n)}
    if kd.store == RF.SCHEDULE:
        job = st.schedules.get(rid)
        if job is None or job.removed:
            return _fail(kind, rid, f"no recorded schedule {rid!r}")
        return {n: getattr(job, n) for n in {*kd.attrs.values(), *(o.name for o in kd.own)}}
    if kd.store == RF.RULE:
        from .rules import rule_get

        got = rule_get(repo, rid)
        if got.exit != O.OK:
            return _fail(kind, rid, f"no rule {rid!r}")
        return dict(got.data)
    rec = st.defs.get(def_key(kind, rid))
    if rec is None or not rec.live:
        return _fail(kind, rid, f"no active {kind} {rid!r}")
    return dict(rec.fields)


def _op_fields(kd: RF.KindFields, rid: str, edit, cur: dict, extra: dict[str, str]):
    """The record fields ``edit`` sets, or a refusal text. ``extra`` is the ``--set`` mapping."""
    fields: dict[str, Any] = {}

    def need(op: str) -> str:
        attr = kd.attrs.get(op)
        if not attr:
            raise LookupError(RF.refusal(kd, rid, op))
        return attr

    try:
        for op in ("title", "priority"):
            value = getattr(edit, op)
            if value is not None:
                fields[need(op)] = value
        if edit.body is not None or edit.append_body:
            attr = need("body")
            base = cur.get(attr) or "" if edit.body is None else edit.body
            if edit.append_body:
                base = (base + "\n\n" if base else "") + edit.append_body
            fields[attr] = base
        for op in RF.LIST_OPS:
            given = getattr(edit, op)
            add, rem = getattr(edit, f"add_{op}"), getattr(edit, f"remove_{op}")
            if given is None and add is None and rem is None:
                continue
            attr = need(op)
            held = (
                list(GS.parse(given))
                if op == "globs" and given is not None
                else (list(given) if given is not None else list(cur.get(attr) or []))
            )
            fields[attr] = GS.adjust(held, add, rem) if (add or rem) else held
        if edit.parent is not None:
            need("parent")
    except LookupError as exc:
        return str(exc)
    for name, raw in extra.items():
        own = kd.own_field(name)
        if own is None:
            return (
                f"{kd.kind} {rid} has no field {name!r} of its own. "
                f"A {kd.kind} accepts: {kd.accepts()}. Nearest verb: {kd.nearest}."
            )
        try:
            fields[name] = RF.coerce(own, raw)
        except ValueError as exc:
            return f"{kd.kind} {rid}: {exc}"
    return fields


def update_record(repo: Path, ref: str, edit, *, agent: str = "") -> O.Outcome | None:
    """Apply ``edit`` to the non-item record ``ref`` names; None when ``ref`` names no record
    (so `items.update` reports its own 'no such item')."""
    log, cfg, st = _load(repo, agent)
    kind, rid, problem = resolve(st, ref)
    if problem:
        return None if not kind and "several" not in problem else O.refused(_EV, problem, id=ref)
    kd = _entry(kind)
    if kd is None:
        return _fail(
            kind, rid, f"unknown record kind {kind!r}: the kinds are {', '.join(RF.ALL_KIND_NAMES)}"
        )
    for op in RF.ITEM_ONLY:
        if getattr(edit, op):
            return O.refused(
                _EV,
                f"{kind} {rid} has no {op.replace('_', '-')} field: it is a work-item field (task, phase). A {kind} accepts: {kd.accepts()}. Nearest verb: {kd.nearest}.",
                id=rid,
                record_kind=kind,
            )
    extra, bad = RF.split_set(edit.set)
    if bad:
        return _fail(kind, rid, bad)
    # `add_*`/`remove_*` given as set items (the MCP carries them there: its schema stays small).
    for name in [n for n in extra if n.partition("_")[0] in ("add", "remove")]:
        op = name.split("_", 1)[1]
        if op in RF.LIST_OPS:
            setattr(edit, name, [p for p in extra.pop(name).split(",") if p])
    with log.transaction():
        _log, _cfg, st = _load(repo, agent)
        cur = _current(repo, st, kind, rid, kd)
        if isinstance(cur, O.Outcome):
            return cur
        fields = _op_fields(kd, rid, edit, cur, extra)
        if isinstance(fields, str):
            return O.refused(_EV, fields, id=rid, record_kind=kind)
        changed = {k: v for k, v in fields.items() if cur.get(k) != v}
        if not changed:
            return O.nothing(_EV, f"{kind} {rid}: nothing to change", id=rid, record_kind=kind)
        return _write(repo, log, cfg, kind, rid, kd, changed, agent)


def _write(repo, log, cfg, kind, rid, kd, changed, agent) -> O.Outcome:
    if kd.store == RF.EVENT:
        log.append(kd.event, rid, {"fields": changed})
        return O.ok(_EV, id=rid, record_kind=kind, changed=sorted(changed), fields=changed)
    if kd.store == RF.RULE:
        from .rules import rule_update

        out = rule_update(repo, rid, agent=agent, **changed)
    elif kd.store == RF.SCHEDULE:
        from .schedule import schedule_update

        out = schedule_update(repo, rid, changed, agent=agent)
    else:
        from . import defs as DEFS
        from . import records as R

        dk = _data_kind(kind)
        if dk is not None:
            out = R.record_edit(repo, dk, rid, changed, agent=agent)
        else:
            out = DEFS.def_update(repo, kind, rid, changed, agent=agent)
    data = {**out.data, "id": rid, "record_kind": kind, "changed": sorted(changed)}
    if out.exit == O.OK:
        return O.ok(_EV, **data)
    make = {O.NOTHING: O.nothing, O.REFUSED: O.refused}.get(out.exit, O.failed)
    return make(_EV, out.reason, **data)
