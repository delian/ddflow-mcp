"""The record lifecycle, built once on the record surface (RC1, RC2 of D-record-surface-contract).

`api.records` gives a kind seven verbs; these are the five that follow a record through its
life, so every kind gets them from its descriptor and none writes its own:

* ``record_history``    the WHOLE chain of a record from the log (not the fold's newest
  entries): who, when, why, in which session, and with ``diff`` the fields before and after;
* ``record_supersede``  ``rid`` is replaced by another; both stay and each names the other;
* ``record_merge``      several records fold into one, in ONE log unit (`EventLog.append_many`):
  the target takes the union of the list fields, a scalar stays unless ``take`` says
  otherwise, the text is the caller's, then each source is ``def.merged``;
* ``record_split``      one record becomes several, in ONE unit: each part is a
  ``def.recorded`` (``provenance.split_from``), then the original is ``def.retired`` with
  ``split_into``. No new event kind: both are additive optional fields (D-compat);
* ``record_restore``    a retired, superseded, merged or split record comes back with the
  fields of a chosen earlier version (``provenance.restored_from``).

Everything is validated before the first byte is written. Text in a history is the log's
(already redacted when written) and is returned as DATA: clipped, never interpreted.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import id_problem
from ..core import defs as D
from ..core import outcome as O
from ..core import textsim
from ..core.model import fold
from ..core.textcut import clip
from . import _dedupe as DD
from . import defs as DEFS
from . import records as R
from ._base import _load
from .records import RecordKind, _as, _event, _exists, _pre

#: Characters of one field value in a history diff.
VALUE_MAX = 400
#: Token overlap at which two parts of one split read as the same record.
PART_SAME = 0.9
#: A split makes at least this many parts.
MIN_PARTS = 2
_PART_SPLIT = re.compile(r",(?=[A-Za-z_][A-Za-z0-9_]*=)")


def _chain(log: Any, key: str) -> list[Any]:
    """The ``def.*`` events of one record, oldest first (lamport, then time, then id)."""
    found = [e for e in log.read_all() if e.subject == key and e.kind in D.EVENT_KINDS]
    return sorted(found, key=lambda e: (e.lamport, e.ts, e.id))


def _value(v: Any) -> Any:
    if isinstance(v, str):
        return clip(v, VALUE_MAX + 6, keep=VALUE_MAX, marker=" [...]")
    if isinstance(v, list):
        return [_value(x) for x in v]
    return v


def _apply(fields: dict[str, Any], ev: Any) -> dict[str, Any]:
    """The fields after a content event: ``recorded`` replaces, ``updated`` merges (None removes)."""
    new = ev.data.get("fields")
    new = new if isinstance(new, dict) else {}
    if ev.kind == "def.recorded":
        return dict(new)
    out = dict(fields)
    for name, value in new.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name) and (name in before) == (name in after):
            continue
        change: dict[str, Any] = {}
        if name in before:
            change["before"] = _value(before[name])
        if name in after:
            change["after"] = _value(after[name])
        out[name] = change
    return out


def _entries(chain: Sequence[Any], *, diff: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fields: dict[str, Any] = {}
    version = 0
    for ev in chain:
        d = ev.data
        prov = d.get("provenance") if isinstance(d.get("provenance"), dict) else {}
        row: dict[str, Any] = {"event": ev.kind, "at": ev.ts, "by": ev.agent, "lamport": ev.lamport}
        if ev.kind in ("def.recorded", "def.updated"):
            version += 1
            row["version"] = version
        for key, src in (
            ("session", prov.get("session")),
            ("prompt", prov.get("prompt")),
            ("reason", d.get("reason")),
            ("digest", d.get("digest")),
            ("successor", d.get("successor")),
            ("split_into", d.get("split_into")),
            ("split_from", prov.get("split_from")),
            ("restored_from", prov.get("restored_from")),
        ):
            if src:
                row[key] = src
        if diff and ev.kind in ("def.recorded", "def.updated"):
            after = _apply(fields, ev)
            row["changes"] = _delta(fields, after)
            fields = after
        out.append(row)
    return out


def record_history(
    repo: Path,
    kind: RecordKind,
    rid: str,
    *,
    diff: bool = False,
    limit: int | None = None,
    agent: str = "",
) -> O.Outcome:
    """The whole chain of one record, oldest first; ``limit`` keeps the newest N (None or 0:
    all of it). With ``diff`` each content entry says which fields changed, before and after."""
    ev = _event(kind, "history")
    if (
        pre := _pre(kind, "history", repo, id=rid, diff=diff, limit=limit, agent=agent)
    ) is not None:
        return pre
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        return O.refused(ev, f"limit must be a whole number, 0 or more; got {limit!r}")
    log, _cfg, st = _load(repo, agent)
    if _exists(st, kind, rid) is None:
        return O.failed(ev, f"no {kind.name} {rid!r}", record_kind=kind.name, id=rid)
    rows = _entries(_chain(log, D.key(kind.def_kind, rid)), diff=diff)
    shown = rows[-limit:] if limit else rows
    return O.ok(
        ev,
        record_kind=kind.name,
        id=rid,
        entries=shown,
        total=len(rows),
        truncated=len(shown) < len(rows),
        text_is_data=True,
    )


def record_supersede(
    repo: Path, kind: RecordKind, rid: str, *, by: str, reason: str, agent: str = ""
) -> O.Outcome:
    """``rid`` is replaced by ``by``; both stay, each names the other in its history."""
    if (
        pre := _pre(kind, "supersede", repo, id=rid, by=by, reason=reason, agent=agent)
    ) is not None:
        return pre
    if not (reason or "").strip():
        return O.failed(_event(kind, "supersede"), "superseding a record needs a reason", id=rid)
    out = DEFS.def_supersede(repo, kind.def_kind, rid, by, reason=reason, agent=agent)
    return _as(out, kind, "supersede", rid)


# -- merge ---------------------------------------------------------------------------


def _union(*lists: Any) -> list[str]:
    out: list[str] = []
    for items in lists:
        for item in items or ():
            if item not in out:
                out.append(item)
    return out


def _text_field(kind: RecordKind) -> str:
    """The field ``--text`` replaces: the first string field the descriptor reads as text."""
    for name in kind.text:
        spec = kind.field(name)
        if spec is not None and spec.type == "string":
            return name
    return ""


def _merged_fields(
    kind: RecordKind, target: Any, sources: list[Any], take: Mapping[str, str], text: str
) -> tuple[dict[str, Any], str]:
    """``(the target's fields after the merge, "")`` or ``({}, why not)``."""
    out = dict(target.fields)
    by_id = {r.id: r for r in (target, *sources)}
    for spec in kind.fields:
        if spec.type == "array":
            union = _union(
                target.fields.get(spec.name), *(s.fields.get(spec.name) for s in sources)
            )
            if union:
                out[spec.name] = union
    for name, rid in take.items():
        spec = kind.field(name)
        if spec is None:
            return {}, f"{kind.name} has no field {name!r} to take"
        if spec.type == "array":
            return {}, f"{name} is merged as a union; there is nothing to take"
        if rid not in by_id:
            return {}, f"take {name}={rid}: {rid!r} is not among the records merged"
        if name not in by_id[rid].fields:
            return {}, f"take {name}={rid}: {rid!r} has no {name}"
        out[name] = by_id[rid].fields[name]
    if text:
        field = _text_field(kind)
        if not field:
            return {}, f"{kind.name} has no text field for --text to replace"
        out[field] = text
    return out, ""


def _take(raw: Sequence[str] | None) -> tuple[dict[str, str], str]:
    out: dict[str, str] = {}
    for item in raw or ():
        name, sep, rid = str(item).partition("=")
        if not sep or not name.strip() or not rid.strip():
            return {}, f"take wants field=id; got {item!r}"
        out[name.strip()] = rid.strip()
    return out, ""


def record_merge(
    repo: Path,
    kind: RecordKind,
    ids: Sequence[str],
    *,
    into: str,
    reason: str,
    text: str = "",
    take: Sequence[str] | None = None,
    allow_weaken: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Fold ``ids`` into ``into`` in one unit: the target is updated (the list fields
    unioned, a scalar kept unless ``take`` names a source, the text the caller's), then each
    source is merged into it. Refused when a record is not active, ``into`` is among the
    sources, or the kind's ``weakening`` hook says the target would be weaker than a source
    (unless ``allow_weaken``)."""
    ev = _event(kind, "merge")
    ids = list(ids or ())
    if (
        pre := _pre(
            kind,
            "merge",
            repo,
            ids=ids,
            into=into,
            reason=reason,
            text=text,
            take=take,
            allow_weaken=allow_weaken,
            agent=agent,
        )
    ) is not None:
        return pre
    if not (reason or "").strip():
        return O.failed(ev, "merging records needs a reason", id=into)
    wanted, why = _take(take)
    if not ids or len(set(ids)) != len(ids) or into in ids:
        why = why or "merge wants one or more different records, none of them the target"
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=into)
    log, cfg, _st = _load(repo, agent)
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        plan = _plan_merge(kind, st, ids, into, wanted, text, allow_weaken)
        if isinstance(plan, O.Outcome):
            return plan
        target, sources, merged = plan
        key = D.key(kind.def_kind, into)
        writes: list[tuple[str, str, dict[str, Any]]] = []
        change = {k: v for k, v in merged.items() if target.fields.get(k) != v}
        change.update({k: None for k in target.fields if k not in merged})
        data = DEFS._update_data(log, cfg, target, change, None, None, reason) if change else None
        if data is not None:
            writes.append(("def.updated", key, data))
        for src in sources:
            body = DEFS._envelope(cfg, kind.def_kind, src.id, src.source, None, log)
            body.update(digest=src.digest, successor=into, reason=reason.strip())
            writes.append(("def.merged", D.key(kind.def_kind, src.id), body))
        log.append_many(writes)
    return O.ok(
        ev,
        record_kind=kind.name,
        id=into,
        merged=ids,
        changed=sorted(change),
        updated=data is not None,
    )


def _plan_merge(
    kind: RecordKind,
    st: Any,
    ids: Sequence[str],
    into: str,
    take: Mapping[str, str],
    text: str,
    allow_weaken: bool,
) -> tuple[Any, list[Any], dict[str, Any]] | O.Outcome:
    """``(target, sources, the target's fields after)`` for a merge that may go ahead, or the
    refusal: a record that is not active, a take or text the kind cannot apply, a weaker target."""
    ev = _event(kind, "merge")
    recs: list[Any] = []
    for rid in (into, *ids):
        rec, refusal = DEFS._live(st, kind.def_kind, rid, "def.merged")
        if refusal is not None:
            return _as(refusal, kind, "merge", rid)
        recs.append(rec)
    target, sources = recs[0], recs[1:]
    merged, why = _merged_fields(kind, target, sources, take, text)
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=into)
    for src in sources if kind.weakening and not allow_weaken else ():
        if weaker := kind.weakening(src.fields, merged):
            return O.refused(
                ev,
                f"merging {src.id} into {into} would weaken it ({weaker}); "
                "say --allow-weaken if that is meant",
                record_kind=kind.name,
                id=into,
            )
    return target, sources, merged


# -- split ---------------------------------------------------------------------------


def parse_part(raw: str, kind: RecordKind) -> tuple[str, dict[str, Any], str]:
    """``newid:field=value[,field=value]`` as ``(id, fields, "")`` or ``("", {}, why not)``.
    An array is a JSON list (``tags=["a","b"]``) or ``|``-separated (``tags=a|b``)."""
    rid, sep, rest = str(raw).partition(":")
    rid = rid.strip()
    if not sep or not rid:
        return "", {}, f"a part is newid:field=value[,field=value]; got {raw!r}"
    fields: dict[str, Any] = {}
    for item in _PART_SPLIT.split(rest) if rest.strip() else []:
        name, eq, value = item.partition("=")
        name = name.strip()
        spec = kind.field(name)
        if not eq or spec is None:
            known = ", ".join(f.name for f in kind.fields)
            return "", {}, f"part {rid}: {item!r} is not a field of {kind.name} ({known})"
        try:
            fields[name] = _typed(spec.type, value.strip())
        except ValueError as exc:
            return "", {}, f"part {rid}: {name}: {exc}"
    return rid, fields, ""


def _typed(kind: str, value: str) -> Any:
    if kind == "integer":
        return int(value)
    if kind == "boolean":
        if value.lower() not in ("true", "false"):
            raise ValueError("true or false")
        return value.lower() == "true"
    if kind == "array":
        if value.startswith("["):
            return json.loads(value)
        return [v.strip() for v in value.split("|") if v.strip()]
    return value


def parts_from_file(repo: Path, path: str, kind: RecordKind) -> tuple[list[tuple[str, dict]], str]:
    """The ``[[part]]`` tables of a TOML file inside the repository: ``id`` and fields."""
    root = Path(repo).resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        return [], f"{path}: the parts file must be inside the repository"
    try:
        doc = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8, or not TOML
        return [], f"{path}: cannot read parts: {exc}"
    rows = doc.get("part")
    if not isinstance(rows, list) or not all(isinstance(r, dict) and r.get("id") for r in rows):
        return [], f"{path}: expected [[part]] tables, each with an id"
    return [(str(r["id"]), {k: v for k, v in r.items() if k != "id"}) for r in rows], ""


def _same_part(a: str, b: str) -> bool:
    ta, tb = set(textsim.tokens(a)), set(textsim.tokens(b))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= PART_SAME


def record_split(
    repo: Path,
    kind: RecordKind,
    rid: str,
    *,
    parts: Sequence[str] = (),
    from_file: str = "",
    reason: str,
    agent: str = "",
) -> O.Outcome:
    """Replace ``rid`` by two or more parts in one unit: each part is recorded (the original's
    fields, then the part's own) with ``provenance.split_from``, then ``rid`` is retired with
    ``split_into``. A part that reads as another part, or as a record already filed (the
    original excepted), is refused naming it."""
    ev = _event(kind, "split")
    if (
        pre := _pre(
            kind,
            "split",
            repo,
            id=rid,
            parts=parts,
            from_file=from_file,
            reason=reason,
            agent=agent,
        )
    ) is not None:
        return pre
    if not (reason or "").strip():
        return O.failed(ev, "splitting a record needs a reason", record_kind=kind.name, id=rid)
    given, why = _given_parts(repo, kind, parts, from_file)
    ids = [p for p, _ in given]
    if not why and len(given) < MIN_PARTS:
        return O.refused(ev, "a split makes two or more parts; to rewrite one record use edit")
    if not why and (len(set(ids)) != len(ids) or rid in ids):
        return O.refused(ev, "the parts need different ids, none of them the original's")
    if not why:
        why = next((f"part id {p!r} {bad}" for p in ids if (bad := id_problem(p))), "")
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=rid)
    log, cfg, _st = _load(repo, agent)
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        orig, refusal = DEFS._live(st, kind.def_kind, rid, "def.retired")
        if refusal is not None:
            return _as(refusal, kind, "split", rid)
        full, bad = _vet_parts(repo, log, cfg, st, kind, orig, given)
        if bad is not None:
            return bad
        writes: list[tuple[str, str, dict[str, Any]]] = []
        for pid, fields in full:
            body = DEFS._envelope(cfg, kind.def_kind, pid, orig.source, {"split_from": rid}, log)
            body.update(fields=dict(fields), digest=D.digest(fields), reason=reason.strip())
            writes.append(("def.recorded", D.key(kind.def_kind, pid), body))
        gone = DEFS._envelope(cfg, kind.def_kind, rid, orig.source, None, log)
        gone.update(digest=orig.digest, reason=reason.strip(), split_into=ids)
        writes.append(("def.retired", D.key(kind.def_kind, rid), gone))
        log.append_many(writes)
    return O.ok(ev, record_kind=kind.name, id=rid, parts=ids)


def _given_parts(
    repo: Path, kind: RecordKind, parts: Sequence[str], from_file: str
) -> tuple[list[tuple[str, dict]], str]:
    """The parts the caller named (``--part`` words, then a file's tables), or why not."""
    given: list[tuple[str, dict]] = []
    for raw in parts or ():
        pid, fields, why = parse_part(raw, kind)
        if why:
            return [], why
        given.append((pid, fields))
    if from_file:
        more, why = parts_from_file(repo, from_file, kind)
        given += more
        return given, why
    return given, ""


def _vet_parts(
    repo: Path, log: Any, cfg: Any, st: Any, kind: RecordKind, orig: Any, given: list
) -> tuple[list[tuple[str, dict]], O.Outcome | None]:
    """Each part as it will be stored (the original's fields under its own), or the refusal:
    an id already filed, a field the kind rejects, a part that reads as an earlier part or as
    a record already filed (the original excepted)."""
    ev = _event(kind, "split")
    refuse = {"record_kind": kind.name, "id": orig.id}
    full: list[tuple[str, dict]] = []
    for pid, own in given:
        if held := _exists(st, kind, pid):
            return [], O.refused(
                ev, f"{kind.name} {pid!r} already exists ({held.status})", **refuse
            )
        stored, why = R._checked(kind, {**orig.fields, **own}, whole=True)
        if why:
            return [], O.failed(ev, f"part {pid}: {why}", **refuse)
        for qid, other in full:
            if _same_part(R._text(kind, stored), R._text(kind, other)):
                return [], O.refused(
                    ev, f"part {pid} reads as part {qid}; make them differ", **refuse
                )
        rec = DD.Record(
            kind=kind.def_kind,
            event_kind="def.recorded",
            rid=D.key(kind.def_kind, pid),
            title=str(stored.get(kind.title, "")),
            body=R._body(kind, stored),
        )
        chk = DD.check_add(repo, log, cfg, st, rec, None)
        rivals = [
            c.get("id")
            for c in (chk.refusal.data.get("candidates", []) if chk.refusal is not None else [])
            if c.get("id") not in (orig.id, D.key(kind.def_kind, orig.id))
        ]
        if rivals:
            why = chk.refusal.reason  # type: ignore[union-attr]
            return [], O.refused(ev, f"part {pid} reads as {rivals[0]}: {why}", **refuse)
        full.append((pid, stored))
    return full, None


# -- restore -------------------------------------------------------------------------


def _version(chain: Sequence[Any], to: str | int | None) -> tuple[Any, dict[str, Any], str]:
    """The content event chosen by ``to`` and the fields as of it, or a reason. ``to`` is a
    version number of the history (``3``), or the lamport of one of its entries
    (``lamport:42``); None is the last version. The two never mix: a lamport is a counter of
    the whole log and often equals a small version number."""
    content = [e for e in chain if e.kind in ("def.recorded", "def.updated")]
    if not content:
        return None, {}, "no earlier version in the log"
    chosen = content[-1]
    if to is not None:
        word, _, num = str(to).strip().rpartition(":")
        if word not in ("", "lamport") or not (num.isascii() and num.isdigit()):
            return None, {}, f"--to wants a version number or lamport:N; got {to!r}"
        n = int(num)
        if word:
            found = [e for e in content if e.lamport == n]
        else:
            found = content[n - 1 : n] if n >= 1 else []
        if not found:
            return None, {}, f"no such version {to}: versions 1..{len(content)}"
        chosen = found[0]
    fields: dict[str, Any] = {}
    for e in content:
        fields = _apply(fields, e)
        if e is chosen:
            break
    return chosen, fields, ""


def record_restore(
    repo: Path,
    kind: RecordKind,
    rid: str,
    *,
    to: str | int | None = None,
    reason: str,
    agent: str = "",
) -> O.Outcome:
    """Bring back a retired, superseded or merged record (a split one too: its parts stay)
    with the fields of ``to`` (a version number of its history, or ``lamport:N``; default
    the last version). Recorded as a new version that says what it restored."""
    ev = _event(kind, "restore")
    if (pre := _pre(kind, "restore", repo, id=rid, to=to, reason=reason, agent=agent)) is not None:
        return pre
    if not (reason or "").strip():
        return O.failed(ev, "restoring a record needs a reason", record_kind=kind.name, id=rid)
    log, _cfg, st = _load(repo, agent)
    rec = _exists(st, kind, rid)
    if rec is None:
        return O.refused(ev, f"no {kind.name} {rid!r}", record_kind=kind.name, id=rid)
    if rec.live:
        return O.refused(
            ev,
            f"{kind.name} {rid!r} is active; edit changes it, revise replaces it",
            record_kind=kind.name,
            id=rid,
        )
    chosen, fields, why = _version(_chain(log, D.key(kind.def_kind, rid)), to)
    if why:
        return O.refused(ev, f"{kind.name} {rid!r}: {why}", record_kind=kind.name, id=rid)
    parts = next((h["split_into"] for h in reversed(rec.history) if h.get("split_into")), [])
    out = DEFS.def_record(
        repo,
        kind.def_kind,
        rid,
        fields,
        source=rec.source,
        provenance={"restored_from": chosen.lamport},
        reason=reason,
        agent=agent,
    )
    if out.exit != O.OK:
        return _as(out, kind, "restore", rid)
    note = {"parts_stay": parts} if parts else {}
    return O.ok(
        ev, record_kind=kind.name, id=rid, restored_from=chosen.lamport, was=rec.status, **note
    )
