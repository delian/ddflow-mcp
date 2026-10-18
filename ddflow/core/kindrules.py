"""What a data-declared record kind says about its records beyond field types.

A lifecycle (a state field, its initial state and the moves allowed between states), links
(fields that name records of another kind), required sources (fields that cite where a
record came from), validators (pattern, bounds, length), a render template and an upgrade
stamp. All plain data (`KindRules`), declared in the kind's TOML (`core.kinddata`) and
checked by the pure functions here; the one thing that needs the log -- whether a linked
record exists -- is asked of a callable the caller gives.

Pure: no I/O.
"""

from __future__ import annotations

import re
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: The longest value a pattern validator will match (a bound on pathological input): a longer
#: one is refused, not matched in part.
MAX_VALUE = 20000


class RulesError(ValueError):
    """A rules declaration that is not one."""


@dataclass(frozen=True)
class Lifecycle:
    """``field`` holds the state; a new record starts in ``initial``; ``transitions`` maps a
    state to the states it may move to (a state with no entry is final)."""

    field: str
    initial: str
    transitions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def states(self) -> tuple[str, ...]:
        seen = [self.initial]
        for src, dests in self.transitions.items():
            seen += [s for s in (src, *dests) if s not in seen]
        return tuple(dict.fromkeys(seen))


@dataclass(frozen=True)
class Link:
    """``field`` names a record (``many``: a list of them) of definition kind ``to``."""

    field: str
    to: str
    many: bool = False


@dataclass(frozen=True)
class Validator:
    """A check on ``field``: a regex it must match, numeric bounds, a longest length."""

    field: str
    pattern: str = ""
    min: int | None = None
    max: int | None = None
    max_length: int | None = None
    message: str = ""


@dataclass(frozen=True)
class KindRules:
    lifecycle: Lifecycle | None = None
    links: tuple[Link, ...] = ()
    #: Fields that cite a record's source: each must be filled when a record is added.
    sources: tuple[str, ...] = ()
    validators: tuple[Validator, ...] = ()
    #: A ``str.format`` template over the record's fields, ``{id}`` and ``{status}``.
    render: str = ""
    #: The descriptor schema version the kind was written for (0: not stamped).
    stamp: int = 0

    def declaration_problems(self, types: Mapping[str, str]) -> list[str]:
        """Why this does not fit a kind whose fields have ``types`` (name -> JSON type)."""
        bad: list[str] = []

        def need(what: str, name: str, *want: str) -> None:
            if name not in types:
                bad.append(f"{what} names no such field {name!r}")
            elif want and types[name] not in want:
                bad.append(f"{what} field {name!r} is a {types[name]}, not {'/'.join(want)}")

        if (lc := self.lifecycle) is not None:
            need("lifecycle", lc.field, "string")
        for link in self.links:
            need("link", link.field, "array" if link.many else "string")
        for name in self.sources:
            need("sources", name, "string")
        for v in self.validators:
            numeric = v.min is not None or v.max is not None
            want = (
                ("integer",)
                if numeric
                else ("string", "array")
                if (v.pattern or v.max_length)
                else ()
            )
            need("validator", v.field, *want)
            if v.pattern:
                try:
                    re.compile(v.pattern)
                except re.error as exc:
                    bad.append(f"validator on {v.field!r}: bad pattern: {exc}")
        if self.render:
            try:
                used = {
                    n for _l, n, _s, _c in string.Formatter().parse(self.render) if n is not None
                }
            except ValueError as exc:
                return [*bad, f"render is not a template: {exc}"]
            bad += [
                f"render names no such field {n!r}"
                for n in sorted(used - set(types) - {"id", "status"})
            ]
        return bad


def _bounds_problem(v: Validator, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return ""
    if v.min is not None and value < v.min:
        return v.message or f"{v.field} must be at least {v.min}"
    if v.max is not None and value > v.max:
        return v.message or f"{v.field} must be at most {v.max}"
    return ""


def _text_problem(v: Validator, item: str) -> str:
    if v.max_length is not None and len(item) > v.max_length:
        return v.message or f"{v.field} is longer than {v.max_length} characters"
    if not v.pattern:
        return ""
    if len(item) > MAX_VALUE:
        return v.message or f"{v.field} is longer than {MAX_VALUE} characters: too long to match"
    if not re.search(v.pattern, item):
        return v.message or f"{v.field} {item[:40]!r} does not match {v.pattern!r}"
    return ""


def _value_problem(v: Validator, value: Any) -> str:
    if why := _bounds_problem(v, value):
        return why
    items = value if isinstance(value, list | tuple) else [value]
    return next((w for i in items if isinstance(i, str) and (w := _text_problem(v, i))), "")


def _lifecycle_problem(
    lc: Lifecycle, fields: dict[str, Any], before: Mapping[str, Any] | None
) -> str:
    old = (before or {}).get(lc.field)
    new = fields.get(lc.field)
    if old is None:
        if new is None:
            fields[lc.field] = lc.initial
        elif new != lc.initial:
            return f"a new record starts {lc.field} {lc.initial!r}, not {new!r}"
    elif new is None:
        fields[lc.field] = old  # a whole replacement keeps the record's place in its life
    elif new != old and new not in lc.transitions.get(old, ()):
        allowed = ", ".join(lc.transitions.get(old, ())) or "none: it is final"
        return f"{lc.field} cannot go from {old!r} to {new!r}; allowed: {allowed}"
    return ""


def _sources_problem(names: tuple[str, ...], fields: dict[str, Any], new: bool) -> str:
    for name in names:
        blank = not fields.get(name) or not str(fields[name]).strip()
        if (name in fields or new) and blank:
            return f"{name} is required: a record cites its source"
    return ""


def _link_problem(link: Link, value: Any, exists: Callable[[str, str], bool]) -> str:
    for target in value if isinstance(value, list | tuple) else ([value] if value else []):
        if not exists(link.to, str(target)):
            return f"{link.field}: no {link.to} {target!r}"
    return ""


def problem(
    rules: KindRules,
    fields: dict[str, Any],
    before: Mapping[str, Any] | None,
    exists: Callable[[str, str], bool],
) -> str:
    """Why ``fields`` (what is about to be stored; ``before`` the stored record, None for a
    new one) breaks ``rules``, or "". ``fields`` may be filled in: a new record without a
    state gets the initial one, a replacement keeps the state it had."""
    checks = [
        _lifecycle_problem(rules.lifecycle, fields, before) if rules.lifecycle else "",
        _sources_problem(rules.sources, fields, before is None),
        *(
            _value_problem(v, fields[v.field])
            for v in rules.validators
            if fields.get(v.field) is not None
        ),
        *(_link_problem(link, fields.get(link.field), exists) for link in rules.links),
    ]
    return next((c for c in checks if c), "")


def render(rules: KindRules, rid: str, status: str, fields: Mapping[str, Any]) -> str:
    """The record as its template shows it ("" when there is no template); a field the
    record lacks is blank, a list is comma-joined."""
    if not rules.render:
        return ""
    values = {
        k: (", ".join(map(str, v)) if isinstance(v, list | tuple) else v) for k, v in fields.items()
    }
    names = {n for _l, n, _s, _c in string.Formatter().parse(rules.render) if n}
    data = dict.fromkeys(names, "") | values | {"id": rid, "status": status}
    return rules.render.format_map(data)


# -- reading the descriptor ------------------------------------------------------------


def _tab(where: str, raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RulesError(f"{where} must be a table")
    if bad := sorted(set(raw) - allowed):
        raise RulesError(f"{where}: unknown key(s) {', '.join(bad)}")
    return raw


def _int(where: str, raw: dict[str, Any], key: str) -> int | None:
    v = raw.get(key)
    if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
        raise RulesError(f"{where}: {key} must be a whole number")
    return v


def from_raw(raw: dict[str, Any]) -> KindRules | None:
    """The `KindRules` of a parsed descriptor (None when it declares none)."""
    if not {"lifecycle", "link", "validator", "sources", "render", "stamp"} & set(raw):
        return None
    lc = None
    if "lifecycle" in raw:
        t = _tab("lifecycle", raw["lifecycle"], {"field", "initial", "transitions"})
        if not isinstance(t.get("field"), str) or not isinstance(t.get("initial"), str):
            raise RulesError("lifecycle needs field and initial (strings)")
        moves = t.get("transitions", {})
        if not isinstance(moves, dict):
            raise RulesError("lifecycle.transitions must be a table of state = [states]")
        for src, dests in moves.items():
            if not isinstance(dests, list) or not all(isinstance(d, str) for d in dests):
                raise RulesError(f"lifecycle.transitions.{src} must be a list of states")
        lc = Lifecycle(t["field"], t["initial"], {s: tuple(d) for s, d in moves.items()})
    links = tuple(_link(i, x) for i, x in enumerate(_list("link", raw)))
    vals = tuple(_validator(i, x) for i, x in enumerate(_list("validator", raw)))
    sources = raw.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise RulesError("sources must be a list of field names")
    render_ = raw.get("render", "")
    if not isinstance(render_, str):
        raise RulesError("render must be a string")
    stamp = _int("kind", raw, "stamp") or 0
    return KindRules(lc, links, tuple(sources), vals, render_, stamp)


def _list(key: str, raw: dict[str, Any]) -> list[Any]:
    v = raw.get(key, [])
    if not isinstance(v, list):
        raise RulesError(f"[[{key}]] must be an array of tables")
    return v


def _link(n: int, raw: Any) -> Link:
    t = _tab(f"link #{n + 1}", raw, {"field", "to", "many"})
    if not isinstance(t.get("field"), str) or not isinstance(t.get("to"), str):
        raise RulesError(f"link #{n + 1} needs field and to")
    return Link(t["field"], t["to"], bool(t.get("many", False)))


def _validator(n: int, raw: Any) -> Validator:
    where = f"validator #{n + 1}"
    t = _tab(where, raw, {"field", "pattern", "min", "max", "max_length", "message"})
    if not isinstance(t.get("field"), str):
        raise RulesError(f"{where} needs a field")
    return Validator(
        t["field"], str(t.get("pattern", "")), _int(where, t, "min"), _int(where, t, "max"),
        _int(where, t, "max_length"), str(t.get("message", "")),
    )  # fmt: skip
