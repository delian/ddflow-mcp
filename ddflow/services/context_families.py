"""Model-family and harness context facts: load, validate, read, age (CC-inventory).

The data is `templates/context_families.toml` and `templates/context_harnesses.toml`.
Each fact is `{v, s, src, d, note?}`; see the header of the first file. Reading rules:

* `usable()` returns a value only for status `verified` or `documented`. `unverified`
  keeps its value as a lead and `none` means documented absent; neither is ever read as
  true. A fact that is not listed is left out and reads as `None` too.
* A model matching no family resolves to `unknown`, which has no facts: never a neighbour.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from ..infra import paths

STATUSES = ("verified", "documented", "unverified", "none")
READABLE = ("verified", "documented")
UNKNOWN = "unknown"
#: Fallback for `[context].facts_max_age_days` until that knob is wired (CC-inventory-wire).
DEFAULT_MAX_AGE_DAYS = 90
_SHOWN = 5  # stale facts named in one doctor note
_FILES = ("context_families.toml", "context_harnesses.toml")
_TABLES = {"context_families.toml": "family", "context_harnesses.toml": "harness"}


def load(templates: Path | None = None) -> dict[str, dict[str, Any]]:
    """`{file name: parsed toml}` for both data files."""
    base = templates or paths.templates_dir()
    return {name: tomllib.loads((base / name).read_text(encoding="utf-8")) for name in _FILES}


def is_fact(node: Any) -> bool:
    return isinstance(node, dict) and "s" in node


def facts(doc: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Every `(table, row, key, fact)` of one parsed file."""
    out = []
    table = "family" if "family" in doc else "harness"
    for row, body in sorted(doc.get(table, {}).items()):
        for key, node in sorted(body.items()):
            if is_fact(node):
                out.append((table, row, key, node))
    return out


def validate(data: dict[str, dict[str, Any]], harness_ids: list[str] | None = None) -> list[str]:
    """Problems, one string each; empty means the tables are well formed."""
    problems: list[str] = []
    for name, doc in data.items():
        sources = doc.get("sources", {})
        for table, row, key, f in facts(doc):
            at = f"{name}: {table}.{row}.{key}"
            if f["s"] not in STATUSES:
                problems.append(f"{at}: status {f['s']!r} is not one of {STATUSES}")
            if f.get("src") not in sources:
                problems.append(f"{at}: source {f.get('src')!r} is not in [sources]")
            try:
                date.fromisoformat(str(f.get("d")))
            except ValueError:
                problems.append(f"{at}: date {f.get('d')!r} is not ISO")
            if f["s"] in READABLE and "v" not in f:
                problems.append(f"{at}: status {f['s']} needs a value")
    rows = data["context_harnesses.toml"].get("harness", {})
    for hid in harness_ids or []:
        if hid not in rows:
            problems.append(
                f"context_harnesses.toml: harness {hid!r} has no row (add one, even empty)"
            )
    if UNKNOWN not in data["context_families.toml"].get("family", {}):
        problems.append("context_families.toml: family 'unknown' is missing")
    return problems


def usable(fact: dict[str, Any] | None) -> Any:
    """The fact's value if it may be believed, else None."""
    if not is_fact(fact) or fact["s"] not in READABLE:
        return None
    return fact.get("v")


def family_row(data: dict[str, dict[str, Any]], family: str) -> dict[str, Any]:
    """The row for a family id; any id not in the table resolves to `unknown`."""
    rows = data["context_families.toml"]["family"]
    return rows.get(family) or rows[UNKNOWN]


def stale(
    data: dict[str, dict[str, Any]],
    today: date | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> list[str]:
    """`table.row.key (N days old)` for each fact older than `max_age_days`."""
    today = today or date.today()
    out = []
    for doc in data.values():
        for table, row, key, f in facts(doc):
            try:
                age = (today - date.fromisoformat(str(f.get("d")))).days
            except ValueError:
                continue
            if age > max_age_days:
                out.append(f"{table}.{row}.{key} ({age} days old)")
    return out


def doctor_notes(max_age_days: int = DEFAULT_MAX_AGE_DAYS, today: date | None = None) -> list[str]:
    """One doctor note naming the stale facts, or none."""
    old = stale(load(), today, max_age_days)
    if not old:
        return []
    shown = ", ".join(old[:_SHOWN]) + (
        f" and {len(old) - _SHOWN} more" if len(old) > _SHOWN else ""
    )
    return [
        f"context facts older than {max_age_days} days: {shown} (re-read the source, update `d`)"
    ]
