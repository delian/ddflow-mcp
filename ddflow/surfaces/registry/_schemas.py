"""Result schemas (D-compat, D-compat-json-views).

The schema key a projected body leads with, the versions and shapes of every tool's
result, and the generated JSON Schemas the parity test pins.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# -- result schemas (D-compat, D-compat-json-views) -----------------------------------

#: The top-level key an object result carries to name its schema: ``"claim@1"``. No payload
#: field is called this (`tests/test_compat_json.py` checks every tool's).
SCHEMA_KEY = "schema"
#: What a tool name loses to become the command name a schema is filed under.
TOOL_PREFIX = "ddflow_"
#: ``command -> n`` for a command whose result shape changed incompatibly (a field removed
#: or retyped) since schema 1. Adding a field never bumps it; the old shape stays available
#: for one minor release. Absent means 1.
SCHEMA_VERSIONS: dict[str, int] = {}
#: The key a non-zero exit's body leads with (`mcp._refusal_body`): any schema may carry it.
REFUSAL_KEY = "refusal"
#: Payload keys whose value is a bare array. These bodies keep their exact shape (no tag);
#: their schema is declared only for the MCP surface. `tests/test_compat_json.py` runs the
#: tools it can and compares.
ARRAY_PAYLOADS = frozenset(
    {
        "hits",
        "found",
        "rows",
        "findings",
        "candidates",
        "due",
        "jobs",
        "observed",
    }
)

#: What a tool's body is, as `result_shape` says.
SHAPES = ("object", "nested", "array", "text", "dynamic")


def command_name(tool: str) -> str:
    """The name a tool's result schema is filed under: ``ddflow_gate_record`` -> ``gate_record``."""
    return tool[len(TOOL_PREFIX) :] if tool.startswith(TOOL_PREFIX) else tool


def schema_version(command: str) -> int:
    """The current version of ``command``'s result schema."""
    return SCHEMA_VERSIONS.get(command, 1)


def schema_tag(command: str) -> str:
    """The value of an object result's ``schema`` key: ``"<command>@<n>"``."""
    return f"{command}@{schema_version(command)}"


def result_shape(payload: Any, *, text: Any = False) -> str:
    """What a tool's body is, from its table entry (`payload` and `text`):

    ``text`` a document, ``dynamic`` decided by the call's arguments (a callable), ``array``
    a bare array (kept exactly as it is), ``nested`` the value of one data field (an object
    that is a record, or null: ``show``, ``decision_show``, ``recall``), ``object`` a projection
    of fields or the whole data dict.
    """
    if text:
        return "dynamic" if callable(text) else "text"
    if callable(payload):
        return "dynamic"
    if isinstance(payload, str) and payload:
        return "array" if payload in ARRAY_PAYLOADS else "nested"
    return "object"


def payload_fields(payload: Any) -> tuple[str, ...]:
    """The field names a projection declares; none for the whole-data and nested forms,
    whose fields the operation decides."""
    return tuple(payload) if isinstance(payload, tuple) else ()


def output_schema(
    command: str, payload: Any, *, text: Any = False, extra: Iterable[str] = ()
) -> dict[str, Any] | None:
    """The JSON Schema (2020-12) of an object result, or None for any other shape.

    Field TYPES are not declared (``{}``: any); the payload field lists name the fields and
    no more, until a type layer exists. Nothing is ``required`` and extra fields are allowed:
    a refusal leads with `refusal`, and a field added within a version must not invalidate
    a result. ``extra`` are the fields a call adds when it has them.
    """
    if result_shape(payload, text=text) != "object":
        return None
    props: dict[str, Any] = {SCHEMA_KEY: {"const": schema_tag(command)}}
    for name in (*payload_fields(payload), *extra, REFUSAL_KEY):
        props.setdefault(name, {})
    return {"type": "object", "properties": props, "additionalProperties": True}


def array_schema(command: str, payload: Any) -> dict[str, Any] | None:
    """The schema of a bare-array result as a document of its own, or None for any other
    shape. A result array has no place to carry a tag, so this is where it is named."""
    if result_shape(payload) != "array":
        return None
    return {
        "title": schema_tag(command),
        "type": "array",
        "items": {},
    }


def result_schemas(
    tools: Mapping[str, Mapping[str, Any]], optional: Iterable[str] = ()
) -> dict[str, dict[str, Any]]:
    """Every tool's declared result: command, tag, shape, payload and generated schemas.

    ``tools`` is the engine's tool table; ``optional`` the fields a projected body adds when
    the call produced them (`mcp._OPTIONAL_KEYS`). The golden file pinning the contract is
    this table (`tests/test_compat_json.py`; ``python -m ddflow.surfaces.tool_table
    --schemas`` prints it).
    """
    table: dict[str, dict[str, Any]] = {}
    optional = tuple(optional)  # read once per tool below
    for tool, spec in tools.items():
        payload, text = spec.get("payload", ""), spec.get("text", False)
        command = command_name(tool)
        row: dict[str, Any] = {
            "command": command,
            "schema": schema_tag(command),
            "shape": result_shape(payload, text=text),
        }
        if isinstance(payload, str) and payload:
            row["payload"] = payload
        elif isinstance(payload, tuple):
            row["payload"] = list(payload)
        extra = optional if isinstance(payload, tuple) else ()
        row["output_schema"] = output_schema(command, payload, text=text, extra=extra)
        row["array_schema"] = array_schema(command, payload)
        table[tool] = row
    return table


def tag_body(body: Any, command: str) -> Any:
    """``body`` with its ``schema`` key placed first when it is an object; any other body
    (an array, text, a scalar, null) comes back unchanged. A refusal body keeps `refusal`
    as its first key (`mcp._refusal_body`: the block a machine reads first, fixed in
    c5c7d9f) and the tag follows it. A body already carrying the key keeps its own value:
    the tag never overwrites a field."""
    if not isinstance(body, dict) or not command or SCHEMA_KEY in body:
        return body
    tag = {SCHEMA_KEY: schema_tag(command)}
    if next(iter(body), None) == REFUSAL_KEY:
        rest = {k: v for k, v in body.items() if k != REFUSAL_KEY}
        return {REFUSAL_KEY: body[REFUSAL_KEY], **tag, **rest}
    return {**tag, **body}
