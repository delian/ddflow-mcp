"""``kind:<export kind>[.<key>]``: an existing export kind's data, through its own template.

``kind:changelog`` is the whole document the kind renders today (byte for byte, its template
override and ``[export.<kind>]`` settings included). ``kind:status.tasks_done`` yields one key
of the kind's data as ``{"value": ...}``.
"""

from __future__ import annotations

from typing import Any

from ..export import registry as export
from .sources import Context, SourceError, SourceKind, register


def _split(arg: str) -> tuple[str, str]:
    name, _, key = arg.partition(".")
    return name, key


def _check(arg: str) -> str:
    name, key = _split(arg)
    if not name:
        raise SourceError("source 'kind' needs an export kind: kind:<name>[.<key>]")
    try:
        export.get(name)
    except export.ExportError as exc:
        raise SourceError(str(exc)) from None
    if key and not key.replace("_", "").isalnum():
        raise SourceError(f"source 'kind': {key!r} is not a data key")
    return arg


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    name, key = _split(arg)
    kind = export.get(name)
    assert ctx.query is not None
    data = export.plain(kind.data(ctx.query, ctx.filters))
    if not key:
        return {**data, "schema_version": kind.schema_version}
    if key not in data:
        raise SourceError(
            f"export kind {name!r} has no data key {key!r}: {', '.join(sorted(data))}"
        )
    return {"value": data[key]}


def _template(arg: str, ctx: Context) -> Any:
    name, key = _split(arg)
    if key:
        return "{{ value }}"
    return export.resolve_template(name, ctx.repo, ctx.overrides)


register(SourceKind("kind", _check, _data, template_for=_template, needs_log=True))
