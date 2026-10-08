"""One record surface, the generated half: the CLI verbs and the MCP tool of a `RecordKind`.

`api.records` answers seven verbs for any family of records. This module turns a
`RecordKind` into the commands that offer them (D-unify 4, B-uni-record-surface), so a
family declares its fields once and gets, with nothing hand-written on either surface:

* ``ddflow <name> list|show|add|edit|remove|search|revise`` -- one `Command` per verb
  the kind offers, each carrying only the parameters its verb takes;
* one MCP tool, ``ddflow_<name>``, with a ``verb`` argument -- the budget for ~8 more
  tools does not exist (`tests/test_mcp_tool_budget.py`), and the per-verb CLI commands
  declare ``via=(tool, verb)`` so the parity test sees them served;
* the same dispatch behind both (`dispatch`): the CLI handler builds the arguments from
  the parsed line, the tool receives them as they came, and both reach `api.records`.

The kind's fields become parameters of the same name (an array repeats; a boolean is
``--flag/--no-flag``). The duplicate-check answer is the one every add takes: the flags
``--new --extends ID --duplicate-of ID --related ID --check`` (`dedupe_flags`), and on the
tool ``relation`` and ``check_only``. A narrowing is ``--where field=value``, repeatable.

Nothing here is registered with the CLI or the tool table: a family wires its kind in
when it moves onto this (B-rules-cli, B-quota-surfaces ...), so no tool is added and no
byte of ``tools/list`` or ``--help`` changes by this module.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable, Mapping
from typing import Any

from ..api import _dedupe as DD
from ..api import records as R
from ..core import outcome as O
from . import dedupe_flags
from .context import Ctx
from .registry import AS_AGENT, Alias, Command, Param
from .render import emit_json

#: Parameter names the surface itself uses; a field may not take one.
RESERVED = frozenset(
    {
        "verb",
        "id",
        "query",
        "mode",
        "where",
        "all",
        "limit",
        "reason",
        "unset",
        "relation",
        "check_only",
        "new",
        "extends",
        "duplicate_of",
        "related",
        "check",
        AS_AGENT,
    }
)
#: The answer flags on the CLI's add, and the same two ideas on the tool.
_ANSWER_CLI = ("new", "extends", "duplicate_of", "related", "check")
_ANSWER_TOOL = ("relation", "check_only")
#: What each verb takes beyond the kind's fields.
_TAKES: dict[str, tuple[str, ...]] = {
    "list": ("where", "all", "limit"),
    "show": ("id",),
    "add": ("id", *_ANSWER_TOOL),
    "edit": ("id", "unset"),
    "remove": ("id", "reason"),
    "search": ("query", "mode", "where", "all", "limit"),
    "revise": ("id", "reason"),
}
#: Verbs that carry the kind's fields.
_WITH_FIELDS = frozenset({"add", "edit", "revise"})
_DONE = {"add": "added", "edit": "edited", "remove": "removed", "revise": "revised"}
_HELP = {
    "list": "List the {n} records, narrowed by --where field=value.",
    "show": "Show one {n} whole, with its history.",
    "add": "File a new {n}; checked for duplicates like every add.",
    "edit": "Change some fields of a {n}; the rest are kept.",
    "remove": "Retire a {n}: kept with its history, no longer in force. Needs a reason.",
    "search": "Search the {n} records by their text: ranked, exact or regex.",
    "revise": "Replace a {n} whole, with a reason; a retired one comes back.",
}


# -- parameters ------------------------------------------------------------------------


def _field_param(spec: R.FieldSpec, *, required: bool) -> Param:
    """A field as a parameter. Required only where the verb needs it (an edit names the
    fields it changes); ``None`` on the CLI means not given."""
    kw: dict[str, Any] = {"type": spec.type, "help": spec.help, "choices": spec.choices}
    if spec.type == "array":
        kw["repeat"] = True
    if spec.type == "boolean":
        kw["action"] = argparse.BooleanOptionalAction
    return Param(spec.name, required=required, **kw)


def _common(name: str, kind: R.RecordKind) -> Param:
    """The surface's own parameters, by name."""
    word = kind.name
    return {
        "id": Param("id", help=f"The {word}'s id.", positional=True),
        "query": Param("query", help="What to look for.", positional=True),
        "mode": Param(
            "mode",
            help="ranked (default) | exact | regex.",
            choices=R.MODES,
            schema_enum=True,
        ),
        "where": Param(
            "where", type="array", help="Narrow by field=value; repeatable.", repeat=True
        ),
        "all": Param("all", type="boolean", help="Include retired records."),
        "limit": Param(
            "limit", type="integer", help=f"Rows shown (default {R.DEFAULT_LIMIT}; 0 = the most)."
        ),
        "reason": Param("reason", help="Why; kept in the history.", required=True),
        "unset": Param("unset", type="array", help="Fields to remove.", repeat=True),
        "relation": Param(
            "relation",
            help="Answer to a 'possible duplicate' refusal: new | extends:ID | duplicate_of:ID | "
            "related:ID. Omit at first.",
            mcp_only=True,
        ),
        "check_only": Param(
            "check_only",
            type="boolean",
            help="Dry run: write nothing, return the candidates.",
            mcp_only=True,
        ),
    }[name]


def _answer_params() -> tuple[Param, ...]:
    """The CLI's duplicate-check flags: `dedupe_flags.add_flags` spelled as parameters, with
    its help text (`tests/test_record_surface.py` compares the two, so they cannot drift)."""
    return (
        Param(
            "new",
            type="boolean",
            help="answer a possible-duplicate refusal: this is a different record, file it",
            cli_only=True,
        ),
        Param(
            "extends",
            help="answer it: this adds to record ID -- appended to ID while it is open and "
            "unclaimed, else filed as a new record linked to it",
            cli_only=True,
        ),
        Param(
            "duplicate_of",
            help="answer it: this is the same thing as record ID (handled like --extends)",
            cli_only=True,
        ),
        Param(
            "related",
            help="answer it: a different record that is related to ID; linked both ways",
            cli_only=True,
        ),
        Param(
            "check",
            type="boolean",
            help="dry run: print the existing records this would be refused as a duplicate "
            "of and write nothing (exit 0 with candidates, 2 with none)",
            cli_only=True,
        ),
    )


def _verb_params(kind: R.RecordKind, verb: str) -> tuple[Param, ...]:
    own = tuple(_common(n, kind) for n in _TAKES[verb] if n not in _ANSWER_TOOL)
    if verb in _WITH_FIELDS:
        need = verb != "edit"
        own += tuple(_field_param(f, required=need and f.required) for f in kind.fields)
    if verb == "add":
        own += _answer_params()
    return own


def _tool_params(kind: R.RecordKind) -> tuple[Param, ...]:
    """Every parameter of every verb, once, none required but ``verb``: which are needed
    depends on the verb, and `dispatch` says."""
    seen: dict[str, Param] = {
        "verb": Param(
            "verb",
            help=f"One of {', '.join(kind.verbs)}.",
            required=True,
            choices=kind.verbs,
            schema_enum=True,
        )
    }
    for verb in kind.verbs:
        for name in _TAKES[verb]:
            seen.setdefault(
                name, dataclasses.replace(_common(name, kind), required=False, positional=False)
            )
    for spec in kind.fields:
        seen.setdefault(spec.name, _field_param(spec, required=False))
    return tuple(p for p in seen.values() if not p.cli_only)


# -- one dispatch for both surfaces -------------------------------------------------------


def _where(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw or ():
        name, sep, value = str(item).partition("=")
        if not sep or not name.strip():
            raise ValueError(f"where takes field=value; got {item!r}")
        out[name.strip()] = value.strip()
    return out


def _fields(kind: R.RecordKind, args: Mapping[str, Any]) -> dict[str, Any]:
    return {f.name: args[f.name] for f in kind.fields if args.get(f.name) is not None}


def _stray(kind: R.RecordKind, verb: str, args: Mapping[str, Any]) -> str:
    """The arguments given that ``verb`` does not take, as a refusal sentence, or ""."""
    takes = set(_TAKES[verb]) | ({f.name for f in kind.fields} if verb in _WITH_FIELDS else set())
    # An absent argument is None, False or empty; a 0 is a value (`limit`).
    given = {
        k
        for k, v in args.items()
        if k not in ("verb", "as_agent") and v is not None and v is not False and v != []
    }
    stray = sorted(given - takes)
    return f"{kind.name} {verb} does not take {', '.join(stray)}" if stray else ""


def answer_of(args: Mapping[str, Any]) -> DD.Answer:
    """The duplicate-check answer a tool call carries (``relation``, ``check_only``)."""
    answer = DD.Answer.parse(str(args.get("relation") or ""))
    return dataclasses.replace(answer, check_only=bool(args.get("check_only")))


def dispatch(
    repo: Any,
    kind: R.RecordKind,
    args: Mapping[str, Any],
    agent: str = "",
    *,
    answer: DD.Answer | None = None,
) -> O.Outcome:
    """Run the verb ``args["verb"]`` of ``kind``. Raises ValueError (a malformed call, as
    every tool does) for a missing or unknown verb or an argument that cannot be read;
    answers a refusal for arguments the verb does not take."""
    verb = args.get("verb")
    if verb not in R.VERBS or verb not in kind.verbs:
        raise ValueError(f"verb must be one of {', '.join(kind.verbs)}; got {verb!r}")
    if stray := _stray(kind, verb, args):
        return O.refused(f"{kind.name}.{verb}", stray)
    rid = str(args.get("id") or "")
    if verb in ("show", "add", "edit", "remove", "revise") and not rid:
        raise ValueError(f"{verb} needs id")
    if verb == "search" and not args.get("query"):
        raise ValueError("search needs query")
    common: dict[str, Any] = {"agent": agent}
    if verb in ("list", "search"):
        common.update(
            filters=_where(args.get("where")), all=bool(args.get("all")), limit=args.get("limit")
        )
    if verb == "list":
        return R.record_list(repo, kind, **common)
    if verb == "search":
        return R.record_search(
            repo, kind, str(args["query"]), mode=args.get("mode") or "ranked", **common
        )
    if verb == "show":
        return R.record_show(repo, kind, rid, **common)
    if verb == "remove":
        return R.record_remove(repo, kind, rid, reason=str(args.get("reason") or ""), **common)
    fields = _fields(kind, args)
    if verb == "add":
        return R.record_add(
            repo,
            kind,
            rid,
            fields,
            answer=answer if answer is not None else answer_of(args),
            **common,
        )
    if verb == "revise":
        return R.record_revise(
            repo, kind, rid, fields, reason=str(args.get("reason") or ""), **common
        )
    fields.update({str(n): None for n in args.get("unset") or ()})
    return R.record_edit(repo, kind, rid, fields, **common)


# -- the CLI side -----------------------------------------------------------------------


def _row_line(kind: R.RecordKind, row: Mapping[str, Any]) -> str:
    title = row.get(kind.title, "")
    return f"{row['id']}  [{row['status']}] {title}".rstrip()


def _human(kind: R.RecordKind, verb: str, out: O.Outcome) -> str:
    data = out.data
    if verb in ("list", "search"):
        lines = [_row_line(kind, r) for r in data.get("rows", [])]
        if data.get("truncated"):
            lines.append(
                f"(showing {data['shown']} of {data['total']}; --limit 0 shows the most a list gives)"
            )
        return "\n".join(lines)
    if verb == "show":
        head = f"{data['id']}  [{data['status']}]"
        body = [f"  {k}: {v}" for k, v in (data.get("fields") or {}).items()]
        hist = [
            f"  {h.get('at', '')}  {h.get('event', '')}  {h.get('by', '')}"
            for h in data.get("history", [])
        ]
        more = data["history_total"] - len(data.get("history", []))
        tail = [f"  ... {more} earlier entries"] if more > 0 else []
        return "\n".join([head, *body, "history:", *tail, *hist])
    return f"{_DONE[verb]} {kind.name} {data.get('id', '')}"


def _emit(c: Ctx, kind: R.RecordKind, verb: str, out: O.Outcome) -> int:
    """Print an outcome the way the other record commands do: a failure's reason on
    stderr; a JSON body with a refusal's reason leading it; else the human lines."""
    if out.exit == O.FAIL:
        print(out.reason, file=sys.stderr)
        return out.exit
    if c.json:
        body = out.body()
        if out.exit == O.REFUSED and isinstance(body, dict):
            lead = {"reason": out.reason, "outcome": O.EXIT_NAMES[O.REFUSED], "exit": O.REFUSED}
            body = {"refusal": lead, **body}
        emit_json(body)
    elif out.exit == O.REFUSED:
        print(out.reason, file=sys.stderr)
    elif out.exit == O.NOTHING:
        print(out.reason)
    else:
        print(_human(kind, verb, out))
    return out.exit


def _args_of(a: argparse.Namespace, kind: R.RecordKind, verb: str) -> dict[str, Any]:
    names = {*_TAKES[verb], *(f.name for f in kind.fields if verb in _WITH_FIELDS)}
    args = {n: getattr(a, n) for n in names if getattr(a, n, None) is not None}
    return {**args, "verb": verb}


def _handler(kind: R.RecordKind, verb: str) -> Callable[[argparse.Namespace, Ctx], int]:
    def handle(a: argparse.Namespace, c: Ctx) -> int:
        agent = c.log.agent_id if getattr(c, "log", None) else ""
        try:
            args = _args_of(a, kind, verb)
            if verb != "add":
                return _emit(c, kind, verb, dispatch(c.repo, kind, args, agent))
            given = [n for n in _ANSWER_CLI[:-1] if getattr(a, n, None)]
            if len(given) > 1:
                raise ValueError(f"answer one of {', '.join(given)}, not several")
            out = dedupe_flags.run(
                a, c, lambda ans: dispatch(c.repo, kind, args, agent, answer=ans)
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return O.FAIL
        done = dedupe_flags.finish(a, c, out)
        return done if done is not None else _emit(c, kind, verb, out)

    return handle


# -- the declarations -------------------------------------------------------------------


def _check(kind: R.RecordKind) -> None:
    clash = sorted(RESERVED & {f.name for f in kind.fields})
    if clash:
        raise ValueError(
            f"record kind {kind.name!r}: field(s) {', '.join(clash)} take a name the surface uses"
        )


def tool_name(kind: R.RecordKind) -> str:
    return f"ddflow_{kind.name}"


def record_commands(kind: R.RecordKind) -> tuple[Command, ...]:
    """The commands of ``kind``: the MCP tool (no CLI path) first, then one CLI command per
    verb the kind offers, each served by the tool."""
    _check(kind)
    tool = tool_name(kind)
    verbs = ", ".join(kind.verbs)
    cmds = [
        Command(
            path=(),
            tool=tool,
            description=f"{kind.summary[:1].upper()}{kind.summary[1:]}. verb: {verbs}.",
            params=_tool_params(kind),
            call=lambda repo, args, agent, _k=kind: dispatch(repo, _k, args, agent),
            payload="",
        )
    ]
    for verb in kind.verbs:
        cmds.append(
            Command(
                path=(kind.name, verb),
                summary=_HELP[verb].format(n=kind.name),
                params=_verb_params(kind, verb),
                via=(tool, verb),
                handler=_handler(kind, verb),
            )
        )
    return tuple(cmds)


def record_groups(kind: R.RecordKind) -> dict[str, str]:
    """The group help `registry.add_commands` takes."""
    return {kind.name: kind.summary}


def record_group_aliases(kind: R.RecordKind, since: str) -> dict[str, tuple[Alias, ...]]:
    """The old words of the kind's group (`add_commands`' ``group_aliases``), each hidden
    and always callable; ``since`` is the release that renamed them."""
    if not kind.aliases:
        return {}
    return {
        kind.name: tuple(Alias("command", old, kind.name, since) for old in kind.aliases),
    }
