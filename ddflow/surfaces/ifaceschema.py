"""Is a new JSON Schema as permissive as the old one? (SU-digest, used by `ifacedigest`.)

A conservative subset check, not a JSON Schema reasoner. `schema_problems` answers "why could a
call valid under the OLD input schema fail under the NEW one"; `output_problems` answers "why
could a client reading a result under the OLD output schema break on the new one". Both err
toward reporting a problem (a possible restart) rather than missing one: a keyword they do not
understand that differs counts as a restriction. Arguments the old schema did not declare are
not part of its contract, so a newly declared optional argument is compatible.

Why conservative: a false "problem" costs an agent restart; a missed one breaks a live agent's
calls. So where the two checks cannot prove a change loosens, they report it.

Accepted limits (decided by the operator; a review finding inside them is not a defect):

- A false "problem" for a change that cannot reject any value in practice, e.g. a `pattern`
  changed on an integer, or a removed optional argument.
- A newly declared optional property is compatible, although a caller that passed an undeclared
  argument of a conflicting type would now fail: undeclared arguments are not the contract.
- `$ref`, `allOf`/`anyOf`/`oneOf`/`not`, `if`/`then`, `patternProperties` and other keywords are
  not reasoned about; a difference in one of them is reported as a possible restriction.
- Schema dialect details (format assertion, draft differences) are not modelled.
"""

from __future__ import annotations

import json
from typing import Any


def _norm(schema: Any) -> dict[str, Any]:
    """A schema as a dict: `true` accepts anything, `false` accepts nothing."""
    if isinstance(schema, dict):
        return schema
    return {} if schema is True or schema is None else {"enum": []}


def _types(schema: dict[str, Any]) -> set[str] | None:
    t = schema.get("type")
    if t is None:
        return None
    return set(t) if isinstance(t, list) else {t}


def _widens(old: set[str], new: set[str]) -> bool:
    """Every type `old` accepts is accepted by `new` (an integer is a number)."""
    return all(t in new or (t == "integer" and "number" in new) for t in old)


#: Validation keywords where a larger value is looser / a smaller value is looser.
_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
#: Keywords that restrict by value: any change (or addition) can reject a call.
_EXACT = ("pattern", "format", "multipleOf", "uniqueItems")
#: Understood by `schema_problems` (compared above or by recursion), or irrelevant to validity.
_KNOWN = {
    "type", "enum", "required", "properties", "items", "const", "additionalProperties",
    *_LOWER_BOUNDS, *_UPPER_BOUNDS, *_EXACT,
    "description", "title", "default", "examples", "$comment", "deprecated", "readOnly",
    "writeOnly", "$schema", "$id",
}  # fmt: skip


def _canon(value: Any) -> str:
    """One JSON value as canonical text: tells 1 from "1" and true from 1, ignores key order."""
    return json.dumps(value, sort_keys=True)


def _jset(values: list[Any]) -> set[str]:
    return {_canon(v) for v in values}


def _additional_problems(old: dict[str, Any], new: dict[str, Any], where: str) -> list[str]:
    """`additionalProperties`: absent, `true` and `{}` are the loosest, `false` the tightest, a
    schema in between."""
    was, now = old.get("additionalProperties", True), new.get("additionalProperties", True)
    if now is True or now in ({}, was) or was is False:
        return []
    if now is False:
        return [f"{where}: additionalProperties restricted"]
    base = was if isinstance(was, dict) else {}
    return [f"{where}: additionalProperties {p}" for p in schema_problems(base, now)]


def _constraint_problems(old: dict[str, Any], new: dict[str, Any], where: str) -> list[str]:
    out: list[str] = []
    if "enum" in new and ("enum" not in old or not _jset(old["enum"]) <= _jset(new["enum"])):
        out.append(f"{where}: enum narrowed")
    for key in _LOWER_BOUNDS:
        if key in new and (key not in old or new[key] > old[key]):
            out.append(f"{where}: {key} tightened")
    for key in _UPPER_BOUNDS:
        if key in new and (key not in old or new[key] < old[key]):
            out.append(f"{where}: {key} tightened")
    for key in _EXACT:
        if key in new and new[key] != old.get(key):
            if not (key == "uniqueItems" and new[key] is False):
                out.append(f"{where}: {key} changed")
    if "const" in new and ("const" not in old or _canon(new["const"]) != _canon(old["const"])):
        out.append(f"{where}: const changed")
    out += _additional_problems(old, new, where)
    for key in sorted((set(old) | set(new)) - _KNOWN):
        if old.get(key) != new.get(key):
            out.append(f"{where}: {key} changed (not understood: assumed to restrict)")
    return out


def schema_problems(old: dict[str, Any], new: dict[str, Any], path: str = "") -> list[str]:
    """Why a call valid under `old` could fail under `new`; empty when `new` accepts all of
    `old`'s calls. Looks at types, required and removed arguments, enums and value bounds,
    `items`, and a constraint the old schema did not have."""
    old, new = _norm(old), _norm(new)
    where = path or "arguments"
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if nt is not None and (ot is None or not _widens(ot, nt)):
        problems.append(f"{where}: type changed from {sorted(ot or [])} to {sorted(nt)}")
    problems += _constraint_problems(old, new, where)
    for name in sorted(set(new.get("required") or []) - set(old.get("required") or [])):
        problems.append(f"{where}: new required argument {name}")
    old_props, new_props = old.get("properties") or {}, new.get("properties") or {}
    for name in sorted(old_props):
        sub = f"{path}.{name}" if path else name
        if name not in new_props:
            problems.append(f"{sub}: argument removed")
        else:
            problems += schema_problems(old_props[name], new_props[name], sub)
    new_items = new.get("items")
    if new_items is False and old.get("items") is not False:
        problems.append(f"{where}[]: items restricted")
    elif isinstance(new_items, dict) and new_items:
        old_items = old.get("items") if isinstance(old.get("items"), dict) else {}
        problems += schema_problems(old_items, new_items, f"{where}[]")
    return problems


def output_problems(old: Any, new: Any, path: str = "") -> list[str]:
    """Why a client reading a result under the OLD output schema could break on the new one:
    the schema is gone, a field it read is gone or no longer required, or its type is no longer
    one the old schema allowed. A field the new schema adds is harmless."""
    if old is None:
        return []
    where = path or "result"
    if new is None:
        return [f"{where}: output schema removed"]
    old, new = _norm(old), _norm(new)
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if ot and (nt is None or not _widens(nt, ot)):
        problems.append(f"{where}: type changed from {sorted(ot)} to {sorted(nt or [])}")
    for name in sorted(set(old.get("required") or []) - set(new.get("required") or [])):
        problems.append(f"{where}: {name} is no longer guaranteed")
    # What the new schema may emit must stay inside what the old one promised: the input
    # constraint check with the two schemas swapped (bounds, enum, const, patterns, keywords).
    problems += _constraint_problems(new, old, where)
    if isinstance(old.get("items"), (dict, bool)) and old["items"] != {}:
        problems += output_problems(old["items"], new.get("items") or {}, f"{where}[]")
    new_props = new.get("properties") or {}
    for name, sub in sorted((old.get("properties") or {}).items()):
        here = f"{path}.{name}" if path else name
        if name not in new_props:
            problems.append(f"{here}: field removed")
        else:
            problems += output_problems(sub, new_props[name], here)
    return problems
