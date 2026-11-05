"""Coercion of a TOML/env scalar into a knob's declared type."""

from __future__ import annotations

import json
from typing import Any

from ._decls import KNOB_STRICTEST_NUMBER
from ._errors import InvalidValue


def csv_list(raw: str | None) -> list[str]:
    """`"a, b ,c"` -> `["a", "b", "c"]`. Empty entries dropped, whitespace stripped.

    One implementation. `cli._csv` and this module's list coercion were the same
    expression written twice, in the two places a user's comma-separated string enters
    the system — the CLI's `--needs a,b` and the env var `DDFLOW_GATES_REQUIRED=a,b`.
    Two parsers for one notation is two answers to "is `a,,b` two items or three".
    """
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _maybe_json(raw: str, want: type) -> Any:
    """Parse ``raw`` as JSON if it looks like JSON of the wanted type, else None.

    Gives env vars a way to express values containing commas, without breaking the
    plain comma-separated form that is pleasanter for simple cases.
    """
    text = raw.strip()
    opener = "[" if want is list else "{"
    if not text.startswith(opener):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"looks like JSON but does not parse: {exc}") from exc
    if not isinstance(parsed, want):
        raise ValueError(f"expected a JSON {want.__name__}, got {type(parsed).__name__}")
    return parsed


def _coerce(raw: Any, typ: Any) -> Any:
    """Coerce a TOML/env scalar into the field's declared type.

    Env vars arrive as strings; TOML arrives typed. ``list[str]`` from the env is
    comma-separated. A bad bool is an error rather than a silent False -- a silently
    dropped knob is the failure class this whole module is arranged against.
    """
    ts = typ if isinstance(typ, str) else getattr(typ, "__name__", str(typ))
    if not isinstance(raw, str):
        return raw
    if _outer_is_dict(ts):
        return _coerce_dict(raw, ts)
    if "bool" in ts:
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if "int" in ts:
        return int(raw)
    if "float" in ts:
        return float(raw)
    if "list" in ts:
        # JSON first: comma-splitting tears any element that CONTAINS a comma, and the
        # default redaction patterns do -- `{16,}` is a regex quantifier. The split
        # produced two invalid regexes, so secrets stopped being redacted while the
        # config still looked set.
        parsed = _maybe_json(raw, list)
        if parsed is not None:
            # Refused, never `str()`-cast: `[null]` loaded as ["None"] (B7506c1124a).
            # Only where the elements are declared strings; a list of anything else
            # (`list[dict[str, str]]`) is returned as JSON gave it.
            if not _is_exactly(ts, "list[str]"):
                return parsed
            if bad := [x for x in parsed if not isinstance(x, str)]:
                raise ValueError(
                    f"expected a JSON list of strings; got {json.dumps(bad[0])} in {raw!r}"
                )
            return parsed
        if "," not in raw:
            return [raw.strip()] if raw.strip() else []
        return csv_list(raw)
    return raw


def _coerce_layer_knob(sec: str, knob: str, raw: Any, typ: Any, lenient: bool) -> Any:
    """`_coerce_knob`, but a FILE value of a `KNOB_STRICTEST_NUMBER` knob that is not a
    number ("ten") is kept as is: its check fails and the strictest value applies."""
    try:
        return _coerce_knob(sec, knob, raw, typ)
    except InvalidValue:
        if not (lenient and f"{sec}.{knob}" in KNOB_STRICTEST_NUMBER):
            raise
        return raw


def _coerce_knob(sec: str, knob: str, raw: Any, typ: Any) -> Any:
    """`_coerce` for a KNOWN knob: a value of the wrong type is an `InvalidValue` naming
    the key, refused like any other invalid value (exit 3 on the write paths)."""
    try:
        return _coerce(raw, typ)
    except InvalidValue:
        raise
    except ValueError as exc:
        raise InvalidValue(f"invalid {sec}.{knob} = {raw!r}: {exc}") from exc


def _is_exactly(ts: str, want: str) -> bool:
    """Is the type spelling ``want`` itself, optionally wrapped as Optional or ``| None``?

    Not a substring test: `list[list[str]]` and `dict[str, dict[str, str]]` CONTAIN
    `list[str]` and `dict[str, str]`, and their elements are not strings (rubber duck,
    critic).
    """
    t = ts.replace(" ", "")
    w = want.replace(" ", "")
    return t in (w, f"Optional[{w}]", f"{w}|None", f"None|{w}")


def _outer_is_dict(ts: str) -> bool:
    """Is the OUTERMOST container in this type spelling a dict?

    `dict[str, list[str]]` and `list[dict[str, str]]` both contain both words, so
    neither a substring test nor branch order alone can tell them apart; `dict`,
    `<class 'dict'>` and `Optional[dict[...]]` do not start with "dict". Whichever
    word comes first is the outer container.
    """
    d, lst = ts.find("dict"), ts.find("list")
    return d >= 0 and (lst < 0 or d < lst)


def _coerce_dict(raw: str, ts: str) -> dict[str, Any]:
    """A dict knob from its env string: JSON, or `k=v,k=v` for string values.

    Checked by the declared type BEFORE `_coerce`'s list branch: `dict[str, list[str]]`
    contains "list", and was comma-split into a list of strings. A list-valued map has
    no comma notation that would not tear its words, so it takes JSON only.
    """
    if not raw.strip():
        return {}  # "" is an empty map, as it is an empty list for a list knob
    lists = (
        f'expected a JSON object of lists of strings, e.g. {{"Phase-ships": ["none"]}}; got {raw!r}'
    )
    parsed = _maybe_json(raw, dict)
    if parsed is not None:
        if "list" not in ts:
            # Refused, never `str()`-cast: `{"core": null}` loaded as {"core": "None"},
            # a family nobody declared, in the map reviewer independence reads
            # (B7506c1124a). Keys are strings already: JSON object keys always are.
            # Only where the values are declared strings (critic): a map of anything
            # else is returned as JSON gave it.
            if not _is_exactly(ts, "dict[str, str]"):
                return dict(parsed)
            if bad := [k for k, v in parsed.items() if not isinstance(v, str)]:
                raise ValueError(
                    f"expected a JSON object of strings; {bad[0]!r} is "
                    f"{json.dumps(parsed[bad[0]])} in {raw!r}"
                )
            return dict(parsed)
        # Refused HERE, not left to a per-knob check, and never `str()`-cast: a string
        # where the type says a list, or `null` where it says a word (read as "None"),
        # passed through as valid-looking strings (critic).
        if not all(
            isinstance(v, list) and all(isinstance(x, str) for x in v) for v in parsed.values()
        ):
            raise ValueError(lists)
        return {str(k): list(v) for k, v in parsed.items()}
    if "list" in ts:
        raise ValueError(lists)
    pairs = [p for p in raw.split(",") if p.strip()]
    bad = [p for p in pairs if "=" not in p]
    if bad:
        # Silently dropping a malformed pair weakens whatever reads the map -- for
        # `agent.families` that is the reviewer-independence check itself.
        raise ValueError(
            f"malformed dict entry {bad[0]!r}: expected key=value. "
            f"Use JSON for values containing commas or '='."
        )
    return dict(p.split("=", 1) for p in pairs)
