"""The catalogue of what `ddflow update` may change, per record kind (DX-update-universal).

One table. The CLI flags, the MCP parameters, the help text, the refusals and the parity test
are all read from it, so a kind cannot gain an operation on one surface and lack it on another.
Pure data and pure functions: no I/O, no log.

An *operation* is one of the generic edits every kind is asked for (`OPS`); the catalogue
says which record field each one means for a kind (a bug's body is its ``summary``, a
decision's is its ``decision``), or that the kind has no such field. Each kind adds its own
fields (a lesson's ``why``, a research note's ``verdict``), set with ``--set field=value``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The generic edits, in the order help lists them.
OPS = (
    "title",
    "body",
    "append_body",
    "tags",
    "needs",
    "globs",
    "parent",
    "priority",
)
#: The operations an ``ItemEdit`` carries that only a work item (task or phase) has: a record of
#: any other kind refuses them naming the kind.
ITEM_ONLY = (
    "line",
    "resources",
    "worktree",
    "landed",
    "landed_paths",
    "kind",
    "queue",
    "allow_unknown_needs",
)
#: The list operations that fold onto a list (``--add-X`` / ``--remove-X``).
LIST_OPS = ("tags", "needs", "globs")

#: How a ``--set`` value is read.
TEXT, STRING, LIST, INT, BOOL, ENUM = "text", "string", "list", "int", "bool", "enum"

#: The stores a kind lives in; `api.record_update` has one writer for each.
ITEM, EVENT, RULE, SCHEDULE, DEF, DATA = "item", "event", "rule", "schedule", "def", "data"


@dataclass(frozen=True)
class Own:
    """One field a kind has of its own, beyond the generic operations."""

    name: str
    type: str = STRING
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class KindFields:
    """What ``update`` may change on one kind of record."""

    kind: str
    store: str
    #: The event that records an edit (store ``event``); "" where the store has its own writer.
    event: str = ""
    #: Generic operation -> the record's field it changes; an operation absent is not accepted.
    attrs: Mapping[str, str] = field(default_factory=dict)
    own: tuple[Own, ...] = ()
    #: The verb to use for what ``update`` cannot do on this kind.
    nearest: str = ""
    #: Why an operation is not accepted, where the reason is not self-evident.
    declined: Mapping[str, str] = field(default_factory=dict)

    @property
    def ops(self) -> tuple[str, ...]:
        """The generic operations this kind accepts (``append_body`` follows ``body``)."""
        got = {op for op in self.attrs if op in OPS}
        if "body" in got:
            got.add("append_body")
        return tuple(op for op in OPS if op in got)

    def own_field(self, name: str) -> Own | None:
        return next((o for o in self.own if o.name == name), None)

    def accepts(self) -> str:
        """Everything this kind accepts, as a sentence fragment."""
        return ", ".join([*self.ops, *(f"set {o.name}=..." for o in self.own)])


_SEVERITIES = ("low", "medium", "high", "critical")
_VERDICTS = ("CONFIRMED", "REFUTED", "THEORETICAL")

#: The records held as events of their own kind, edited by ``<kind>.updated``.
EVENT_KINDS: dict[str, KindFields] = {
    k.kind: k
    for k in (
        KindFields(
            "bug",
            EVENT,
            "bug.updated",
            {"title": "title", "body": "summary", "tags": "tags", "globs": "globs"},
            (Own("severity", ENUM, _SEVERITIES), Own("item")),
            nearest="ddflow bug tag / ddflow bug fixed / ddflow bug reopen",
        ),
        KindFields(
            "lesson",
            EVENT,
            "lesson.updated",
            {"title": "title", "body": "rule", "tags": "tags", "globs": "globs"},
            (
                Own("why", TEXT),
                Own("how", TEXT),
                Own("summary", TEXT),
                Own("pattern"),
                Own("seen_in", LIST),
            ),
            nearest="ddflow lesson add",
        ),
        KindFields(
            "research",
            EVENT,
            "research.updated",
            {"title": "question", "body": "claim", "tags": "tags"},
            (
                Own("mechanism", TEXT),
                Own("falsifier", TEXT),
                Own("probe", TEXT),
                Own("probe_output", TEXT),
                Own("verdict", ENUM, _VERDICTS),
                Own("budget"),
                Own("sources", LIST),
                Own("item"),
            ),
            nearest="ddflow research add",
        ),
        KindFields(
            "decision",
            EVENT,
            "decision.updated",
            {"title": "title", "body": "decision", "tags": "tags", "globs": "globs"},
            (
                Own("context", TEXT),
                Own("consequences", TEXT),
                Own("alternatives", TEXT),
                Own("sources", LIST),
                Own("item"),
            ),
            nearest="ddflow decision supersede",
        ),
        KindFields(
            "memory",
            EVENT,
            "memory.updated",
            {"body": "text", "tags": "tags"},
            (Own("source"),),
            nearest="ddflow memory add / ddflow memory forget",
            declined={"title": "a memory is one text: its title is its body"},
        ),
        KindFields(
            "session",
            EVENT,
            "session.updated",
            {"body": "summary"},
            nearest="ddflow session note (a note is a log entry of its own) / ddflow session end",
            declined={"title": "a session has no title; a note is a log entry, never rewritten"},
        ),
    )
}

#: Rules live in a file and a definition; `api.rules` is their one writer.
RULE_FIELDS = KindFields(
    "rule",
    RULE,
    "",
    {"title": "title", "body": "content", "tags": "tags", "globs": "globs", "priority": "priority"},
    (Own("enforcement", ENUM, ("advisory", "warn", "block")),),
    nearest="ddflow rule edit",
)
SCHEDULE_FIELDS = KindFields(
    "schedule",
    SCHEDULE,
    "",
    {
        "title": "title",
        "body": "prompt",
        "tags": "tags",
        "needs": "needs",
        "globs": "scope_globs",
    },
    (
        Own("mode", ENUM, ("report", "fix")),
        Own("concurrency_group"),
        Own("missed", ENUM, ("skip", "once")),
        Own("jitter", INT),
        Own("enabled", BOOL),
        Own("escalate", BOOL),
    ),
    nearest="ddflow schedule update",
)
TRIGGER_FIELDS = KindFields(
    "trigger",
    DEF,
    "",
    {"title": "title", "tags": "tags"},
    (
        Own("event"),
        Own("key"),
        Own("count", INT),
        Own("max_open", INT),
        Own("hop_limit", INT),
        Own("enabled", BOOL),
    ),
    nearest="ddflow schedule trigger list",
    declined={"body": "a trigger is a condition and an action: it has no text body"},
)
ITEM_FIELDS = tuple(
    KindFields(
        k,
        ITEM,
        f"{k}.updated",
        {
            "title": "title",
            "body": "body",
            "tags": "tags",
            "needs": "needs",
            "globs": "globs",
            "parent": "parent",
            "priority": "priority",
        },
        nearest="ddflow update",
    )
    for k in ("task", "phase")
)

#: Kinds declared as data (`templates/kinds`, `.ddflow/kinds`), written as definitions; their
#: fields come from the declaration (`api.record_update.data_fields`). These names are the ones
#: the project's operator listed: a project that declares one gets it with no further code.
DATA_KIND_NAMES = ("goal", "principle", "waiver")

#: Every kind with a fixed entry, in the order help lists them.
KINDS: dict[str, KindFields] = {
    **{k.kind: k for k in ITEM_FIELDS},
    **EVENT_KINDS,
    "rule": RULE_FIELDS,
    "schedule": SCHEDULE_FIELDS,
    "trigger": TRIGGER_FIELDS,
}
#: Every kind name ``update`` knows: the fixed ones, then the data kinds.
ALL_KIND_NAMES = (*KINDS, *DATA_KIND_NAMES)


def refusal(kd: KindFields, rid: str, op: str) -> str:
    """Why ``rid`` (of kind ``kd``) cannot take ``op``: names the kind, the field and the nearest
    verb, as every refusal of ``update`` does."""
    why = kd.declined.get(op, "")
    where = f"; {why}" if why else ""
    near = f" Nearest verb: {kd.nearest}." if kd.nearest else ""
    return (
        f"{kd.kind} {rid} has no {op.replace('_', '-')} field{where}. "
        f"A {kd.kind} accepts: {kd.accepts() or 'nothing'}.{near}"
    )


def coerce(own: Own, raw: Any) -> Any:
    """``raw`` (the text after ``=``) as the value of ``own``; raises ValueError naming why."""
    if own.type == LIST:
        if isinstance(raw, list):
            return [str(v) for v in raw]
        return [p.strip() for p in str(raw).split(",") if p.strip()]
    if own.type == INT:
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{own.name} is a whole number; got {raw!r}") from None
    if own.type == BOOL:
        text = str(raw).strip().lower()
        if text not in ("true", "false"):
            raise ValueError(f"{own.name} is true or false; got {raw!r}")
        return text == "true"
    text = str(raw)
    if own.type == ENUM and text not in own.choices:
        raise ValueError(f"{own.name} is one of {', '.join(own.choices)}; got {raw!r}")
    return text


def split_set(items: list[str] | None) -> tuple[dict[str, str], str]:
    """``--set`` items (``field=value``) as a mapping, or why they are not one. A repeated name
    keeps the last."""
    out: dict[str, str] = {}
    for item in items or ():
        name, sep, raw = str(item).partition("=")
        if not sep or not name.strip():
            return {}, f"set takes field=value; got {item!r}"
        out[name.strip()] = raw
    return out, ""
