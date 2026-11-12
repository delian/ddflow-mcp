"""`ddflow correct`: replace a wrongly recorded value with history kept (REC-correct-surface).

One writer of `record.corrected` (REC-correct-event holds the strict fold). The permission model
is D-correction-permissions-2: a person corrects anything; an agent corrects a record it
authored and a record on an item it holds a lease on, without asking; anything else needs the
operator (a T2 approval of exactly this correction). Every refusal names the nearest verb.

The old text is NOT removed: the log is append-only, so the event holds the new value and the
reason, and the audit entry keeps digests only when the writer asked for them. `--secret` says
the value may be a secret, which keeps every digest off. A scrub removes the old text.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core import digest as D
from ..core import outcome as O
from ..core import record_fields as RF
from ..core.handlers import record_update as RU
from ..services import identity as IDN
from ..services.approvals import consume as CN
from ..services.approvals.canon import Canon
from . import _base

VERB = "record.correct"

#: What a refusal points at: the verb that edits a record when nothing was recorded wrongly,
#: and the one that asks for a person.
NEAREST = "ddflow correct <id> --field NAME --text TEXT --reason WHY"

#: What the old text's survival costs, said on every success so nobody mistakes a correction
#: for a removal.
FOLLOW_UPS = (
    "The old text is still in the event log: a correction replaces what the record shows, it "
    "removes nothing. If the old value was a secret, a physical scrub of the log removes it (a "
    "person's act, never an agent's) and the secret itself must be rotated at its source."
)


def _kinds_of(st: Any, rid: str) -> list[str]:
    """Every correctable kind that holds a record named ``rid``."""
    return [k for k in RU.CORRECTABLE if RU._record_of(st, k, rid) is not None]


def _field_name(kind: str, name: str) -> str:
    """The record's field ``name`` means: a generic operation (`body`, `title`) maps to the
    kind's own field, so `--field body` works on a bug, a decision and a task alike."""
    kd = RF.KINDS[kind]
    return kd.attrs.get(name, name)


def _allowed(kind: str) -> set[str]:
    kd = RF.KINDS[kind]
    return {*kd.attrs.values(), *(o.name for o in kd.own), *RU.CORRECT_ONLY.get(kind, ())}


def _coerce(kind: str, name: str, raw: Any) -> Any:
    """``raw`` as the value of field ``name``: a list field splits on commas, a number is read."""
    kd = RF.KINDS[kind]
    own = kd.own_field(name)
    if own is not None:
        return RF.coerce(own, raw)
    op = next((o for o, f in kd.attrs.items() if f == name), "")
    if op in RF.LIST_OPS:
        return RF.coerce(RF.Own(name, RF.LIST), raw)
    if op == "priority":
        return RF.coerce(RF.Own(name, RF.INT), raw)
    return str(raw)


def _author(log, kind: str, rid: str) -> str:
    """The agent that first recorded ``rid``: the agent of the first event on its subject."""
    for ev in log.read_all():
        if ev.subject == rid and ev.kind.startswith(f"{kind}."):
            return ev.agent
    return ""


def _holds(st: Any, kind: str, rid: str, rec: Any, agent: str, grace_s: int) -> bool:
    """Does ``agent`` hold a live lease on the item the record is on (the item itself for a
    task or a phase)?"""
    import time

    item = rid if kind in ("task", "phase") else str(getattr(rec, "item", "") or "")
    it = st.items.get(item) if item else None
    lease = getattr(it, "lease", None)
    return bool(lease and lease.holder == agent and lease.live(time.time(), grace_s))


def _check_request(rc: Any, reason: str, fields: Mapping[str, Any]) -> O.Outcome | None:
    """The refusal for a request that cannot be a correction whatever the record is."""
    if not reason:
        return O.failed(
            VERB, f"a reason is required: say why the value was wrong, never what it was. {NEAREST}"
        )
    if len(reason) > rc.correct_reason_max_chars:
        return O.failed(
            VERB,
            f"the reason is {len(reason)} characters; at most {rc.correct_reason_max_chars} "
            "([records].correct_reason_max_chars). Say why it was wrong, not what it said. "
            f"{NEAREST}",
        )
    if not fields:
        return O.failed(VERB, f"name a field and its new text. {NEAREST}")
    if len(fields) > rc.correct_max_fields:
        return O.failed(
            VERB,
            f"{len(fields)} fields; at most {rc.correct_max_fields} "
            f"([records].correct_max_fields). {NEAREST}",
        )
    return None


def _resolve_kind(st: Any, rid: str, kind: str) -> tuple[str, O.Outcome | None]:
    """(kind, refusal): the one correctable kind ``rid`` is, or why it is none or several."""
    kinds = _kinds_of(st, rid)
    if kind:
        if kind not in RU.CORRECTABLE:
            return "", O.failed(
                VERB, f"kind {kind!r} cannot be corrected; one of {', '.join(RU.CORRECTABLE)}"
            )
        kinds = [k for k in kinds if k == kind]
    if not kinds:
        return "", O.failed(
            VERB,
            f"no correctable record {rid!r}{f' of kind {kind}' if kind else ''}. A rule, a "
            "schedule or a trigger is a definition: `ddflow update` edits it. "
            f"Correctable: {', '.join(RU.CORRECTABLE)}.",
        )
    if len(kinds) > 1:
        return "", O.failed(VERB, f"{rid} is a {' and a '.join(kinds)}: name one with --kind")
    return kinds[0], None


def _new_values(kind: str, fields: Mapping[str, Any]) -> tuple[dict[str, Any], O.Outcome | None]:
    """The fields as the record's own names and types, or why they are not."""
    try:
        new = {
            _field_name(kind, str(n)): _coerce(kind, _field_name(kind, str(n)), v)
            for n, v in fields.items()
        }
    except ValueError as exc:
        return {}, O.failed(VERB, f"{exc}. {NEAREST}")
    bad = sorted(set(new) - _allowed(kind))
    if bad:
        return {}, O.failed(
            VERB,
            f"a {kind} has no field {', '.join(bad)}; a {kind} accepts "
            f"{', '.join(sorted(_allowed(kind)))}. {NEAREST}",
        )
    return new, None


def _permission(
    log: Any,
    cfg: Any,
    st: Any,
    kind: str,
    rid: str,
    new: Mapping[str, Any],
    *,
    reason: str,
    approval: str,
    agent: str,
) -> tuple[dict[str, Any], O.Outcome | None]:
    """(stamp, refusal): who may correct. A person may; an agent may its own record and one
    on an item it holds; any other correction is the operator's, by an approval."""
    if not IDN.agent_marker(agent):
        return {}, None
    me = log.agent_id
    rec = RU._record_of(st, kind, rid)
    if _author(log, kind, rid) == me or _holds(st, kind, rid, rec, me, cfg.lease.grace_s):
        return {}, None
    names = ",".join(sorted(new))
    canon = Canon(
        verb=VERB,
        agent=me,
        item=rid,
        target=names,
        args={"kind": kind, "reason": reason},
        state=D.content_digest(f"{kind}:{rid}:{names}"),
    )
    decided = CN.operator_gate(log, cfg, canon, approval_id=approval, reason=reason, auto=False)
    if decided.ok:
        return {"approval": decided.stamp}, None
    return {}, O.refused(
        VERB,
        f"{decided.message} {rid} was recorded by another agent and is on an item you do not "
        f"hold: only the operator corrects it. Nearest verb: {NEAREST}.",
        id=rid,
        approval=decided.ask_id,
    )


def correct(
    repo: Path,
    rid: str,
    *,
    fields: Mapping[str, Any],
    reason: str,
    kind: str = "",
    secret: bool = False,
    approval: str = "",
    agent: str = "",
) -> O.Outcome:
    """Correct ``fields`` of the record ``rid``; see the module docstring for who may."""
    log, cfg, st = _base._load(repo, agent)
    rc = cfg.records
    reason = (reason or "").strip()
    if (refusal := _check_request(rc, reason, fields)) is not None:
        return refusal
    kind, refusal = _resolve_kind(st, rid, kind)
    if refusal is not None:
        return refusal
    new, refusal = _new_values(kind, fields)
    if refusal is not None:
        return refusal
    stamp, refusal = _permission(
        log, cfg, st, kind, rid, new, reason=reason, approval=approval, agent=agent
    )
    if refusal is not None:
        return refusal
    log.append(
        RU.KIND,
        rid,
        {
            "kind": kind,
            "fields": new,
            "reason": reason,
            "digest_chars": rc.correct_digest_chars,
            "min_chars": rc.correct_min_digest_chars,
            # A digest of a value that may be a secret confirms a guess: off whenever the writer
            # said so, and off unless a knob turned it on.
            "digest_old": bool(rc.correct_digest_old) and not secret,
            "digest_new": bool(rc.correct_digest_new) and not secret,
            **stamp,
        },
    )
    return O.ok(
        VERB,
        id=rid,
        record_kind=kind,
        fields=sorted(new),
        why=reason,
        old_text_in_log=True,
        follow_ups=FOLLOW_UPS,
    )


def correct_one(
    repo: Path,
    rid: str,
    *,
    field: str,
    text: str,
    reason: str,
    kind: str = "",
    secret: bool = False,
    approval: str = "",
    agent: str = "",
) -> O.Outcome:
    """The one-field form both surfaces call (`ddflow correct ID --field F --text T`)."""
    fields = {field: text} if field else {}
    return correct(
        repo,
        rid,
        fields=fields,
        reason=reason,
        kind=kind,
        secret=secret,
        approval=approval,
        agent=agent,
    )
