"""``query:<record query>``: a list of records from the log, with no code (see `recordquery`)."""

from __future__ import annotations

from typing import Any

from . import recordquery
from .sources import Context, SourceError, SourceKind, register

TEMPLATE = (
    "{% for r in rows %}\n"
    "- {{ r.id }}{% if r.title %} {{ r.title | md_escape }}{% elif r.summary %}"
    " {{ r.summary | md_escape }}{% endif %}{% if r.state %} ({{ r.state }}){% endif %}\n"
    "{% endfor %}\n"
)


def _check(arg: str) -> str:
    try:
        recordquery.parse(arg)
    except recordquery.QueryError as exc:
        raise SourceError(f"source 'query': {exc}") from None
    return arg


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    assert ctx.query is not None
    rows = recordquery.run(recordquery.parse(arg), ctx.query)
    return {"rows": rows, "total": len(rows)}


register(SourceKind("query", _check, _data, TEMPLATE, needs_log=True))
