"""``fragments:<dir>``: the tracked Markdown files of a directory, one entry each.

Fragments are files (``docs/fragments/**``), not log events: diffable, reviewable, and parallel
agents each add a file instead of editing the same lines. Entries come in file name order, so
the same tree gives the same bytes. Each file is capped (`Context.max_bytes`).
"""

from __future__ import annotations

from typing import Any

from .sources import (
    Context,
    SourceError,
    SourceKind,
    check_relative,
    read_capped,
    register,
    tracked_in,
)

TEMPLATE = "{% for f in fragments %}\n{{ f.text | trim }}\n\n{% endfor %}\n"
SUFFIXES = (".md", ".markdown")


def _check(arg: str) -> str:
    return check_relative(arg.rstrip("/"), "fragments")


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    base = arg.rstrip("/")
    out = []
    listed = tracked_in(ctx, base)
    if not listed and not (ctx.repo and (ctx.repo / base).is_dir()):
        raise SourceError(f"{base!r} is not a directory of the repo")  # a typo is not 'none yet'
    for rel in listed:
        if not rel.lower().endswith(SUFFIXES) or rel.rsplit("/", 1)[-1].lower().startswith(
            "readme"
        ):
            continue
        out.append(
            {"name": rel.removeprefix(base + "/"), "path": rel, "text": read_capped(ctx, rel)}
        )
    return {"fragments": out, "total": len(out)}


register(SourceKind("fragments", _check, _data, TEMPLATE))
