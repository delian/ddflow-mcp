"""A record kind written as data: the TOML descriptor, parsed and checked.

A kind is a table of scalars and lists (name, summary, title, text, columns, filters,
verbs, search / recall / resource, applies, ``[export]``, ``[feed]``) plus one ``[[field]]``
per field. This module reads that text into a plain mapping of the keyword arguments
`api.records.RecordKind` takes, and refuses what it cannot make sense of with a message that
names the key. Python stays for kinds with behaviour (``ops``, ``weakening``): those keys are
not data and are refused here.

Pure: no I/O besides `tomllib` on the text it is given; it does not import the API.
"""

from __future__ import annotations

import tomllib
from typing import Any

#: Keep in step with `api.records.FIELD_TYPES`, which a field is finally checked against.
FIELD_TYPES = ("string", "integer", "boolean", "array")
_FIELD_KEYS = {"name", "type", "help", "required", "choices", "default"}
_LISTS = ("text", "columns", "filters", "verbs", "aliases", "applies")
_BOOLS = ("search", "recall", "resource")
_STRINGS = ("recall_hint", "declined_other")
_KEYS = {
    "name", "def_kind", "summary", "title", "recall_cap", "declined", "field", "export",
    "feed", *_LISTS, *_BOOLS, *_STRINGS,
}  # fmt: skip
#: Keys that are code, not data: a kind that needs one is written in Python.
CODE_KEYS = ("ops", "weakening")


class KindDataError(ValueError):
    """A descriptor that is not a record kind."""


def _strings(where: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise KindDataError(f"{where} must be a list of strings; got {value!r}")
    return list(value)


def _field(n: int, raw: Any) -> dict[str, Any]:
    where = f"field #{n}"
    if not isinstance(raw, dict):
        raise KindDataError(f"{where} must be a table")
    if bad := sorted(set(raw) - _FIELD_KEYS):
        raise KindDataError(f"{where}: unknown key(s) {', '.join(bad)}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise KindDataError(f"{where} needs a name")
    where = f"field {name!r}"
    ftype = raw.get("type", "string")
    if ftype not in FIELD_TYPES:
        raise KindDataError(f"{where}: type {ftype!r} is not one of {', '.join(FIELD_TYPES)}")
    out: dict[str, Any] = {"name": name, "type": ftype}
    if "help" in raw:
        out["help"] = str(raw["help"])
    if "required" in raw:
        if not isinstance(raw["required"], bool):
            raise KindDataError(f"{where}: required is true or false")
        out["required"] = raw["required"]
    if "choices" in raw:
        out["choices"] = tuple(_strings(f"{where}: choices", raw["choices"]))
    if "default" in raw:
        default = raw["default"]
        out["default"] = tuple(default) if isinstance(default, list) else default
    return out


def _table(where: str, raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KindDataError(f"{where} must be a table")
    if bad := sorted(set(raw) - allowed):
        raise KindDataError(f"{where}: unknown key(s) {', '.join(bad)}")
    return raw


def parse(text: str, *, source: str = "") -> dict[str, Any]:
    """The descriptor in ``text`` as `RecordKind` keyword arguments (``fields`` a list of
    field mappings, ``export`` / ``feed`` mappings or absent, ``declined_other`` the reason
    every verb not offered is declined with). Raises `KindDataError`."""
    at = f" ({source})" if source else ""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise KindDataError(f"kind descriptor{at} is not TOML: {exc}") from None
    try:
        return from_mapping(raw)
    except KindDataError as exc:
        raise KindDataError(f"kind descriptor{at}: {exc}") from None


def _scalars(raw: dict[str, Any], out: dict[str, Any]) -> None:
    for key in _LISTS:
        if key in raw:
            out[key] = tuple(_strings(key, raw[key]))
    for key in _BOOLS:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise KindDataError(f"{key} is true or false")
            out[key] = raw[key]
    for key in ("title", *_STRINGS):
        if key in raw:
            if not isinstance(raw[key], str):
                raise KindDataError(f"{key} must be a string")
            out[key] = raw[key]
    if "recall_cap" in raw:
        cap = raw["recall_cap"]
        if isinstance(cap, bool) or not isinstance(cap, int):
            raise KindDataError("recall_cap must be a whole number")
        out["recall_cap"] = cap
    if "declined" in raw:
        why = raw["declined"]
        if not isinstance(why, dict):
            raise KindDataError("declined must be a table of verb = reason")
        # the verb names are `api.records.VERBS`; RecordKind refuses an unknown one
        out["declined"] = {str(v): str(r) for v, r in why.items()}


def from_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    if code := [k for k in CODE_KEYS if k in raw]:
        raise KindDataError(f"{code[0]} is code; a kind with behaviour is written in Python")
    if bad := sorted(set(raw) - _KEYS):
        raise KindDataError(f"unknown key(s) {', '.join(bad)}")
    for need in ("name", "summary"):
        if not isinstance(raw.get(need), str) or not raw[need].strip():
            raise KindDataError(f"{need} is required")
    out: dict[str, Any] = {"name": raw["name"], "summary": raw["summary"]}
    out["def_kind"] = str(raw.get("def_kind") or raw["name"])
    _scalars(raw, out)
    fields = raw.get("field")
    if not isinstance(fields, list) or not fields:
        raise KindDataError("at least one [[field]] is required")
    out["fields"] = [_field(i + 1, f) for i, f in enumerate(fields)]
    for key in ("export", "feed"):
        if key in raw:
            allowed = {"text", "meta", "title"} if key == "export" else {"binding", "summary"}
            tab = _table(key, raw[key], allowed)
            out[key] = {
                k: (tuple(_strings(f"{key}.{k}", v)) if isinstance(v, list) else v)
                for k, v in tab.items()
            }
    return out
