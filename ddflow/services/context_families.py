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

from ..core import clock
from ..infra import paths

FACT_KEYS = ("v", "s", "src", "d", "note", "exc", "only")
STATUSES = ("verified", "documented", "unverified", "none")
READABLE = ("verified", "documented")
UNKNOWN = "unknown"
_SHOWN = 5  # stale facts named in one doctor note
_FILES = ("context_families.toml", "context_harnesses.toml")


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
    rows = doc.get(table, {})
    for row, body in sorted(rows.items() if isinstance(rows, dict) else []):
        if not isinstance(body, dict):
            continue
        for key, node in sorted(body.items()):
            if is_fact(node):
                out.append((table, row, key, node))
    return out


def _field_problems(at: str, f: dict[str, Any]) -> list[str]:
    """Unknown fields and wrong types of `exc` / `only`; empty keys would match every model."""
    out = []
    if set(f) - set(FACT_KEYS):
        out.append(f"{at} has unknown field(s) {sorted(set(f) - set(FACT_KEYS))}")
    exc, only = f.get("exc", {}), f.get("only", [])
    if not isinstance(exc, dict) or not all(k for k in exc):
        out.append(f"{at}: exc must be a table of non-empty model substrings")
    if not isinstance(only, list) or not all(isinstance(k, str) and k for k in only):
        out.append(f"{at}: only must be a list of non-empty model substrings")
    return out


def _shape_problems(name: str, doc: dict[str, Any]) -> list[str]:
    """Row entries that are not facts (silently ignored otherwise) or carry unknown fields."""
    out = []
    for table in ("family", "harness"):
        rows = doc.get(table, {})
        if not isinstance(rows, dict):
            out.append(f"{name}: {table} is not a table")
            continue
        for row, body in rows.items():
            if not isinstance(body, dict):
                out.append(f"{name}: {table}.{row} is not a table")
                continue
            for key, node in body.items():
                at = f"{name}: {table}.{row}.{key}"
                if key != "note" and not is_fact(node):
                    out.append(f"{at} is not a fact (no `s`) and would be ignored")
                elif is_fact(node):
                    out += _field_problems(at, node)
    return out


def _fact_problems(name: str, doc: dict[str, Any]) -> list[str]:
    """Status, source, date and value rules for each fact of one file."""
    out = []
    for table, row, key, f in facts(doc):  # facts() skips non-table rows (see _shape_problems)
        at = f"{name}: {table}.{row}.{key}"
        if f["s"] not in STATUSES:
            out.append(f"{at}: status {f['s']!r} is not one of {STATUSES}")
        if f.get("src") not in doc.get("sources", {}):
            out.append(f"{at}: source {f.get('src')!r} is not in [sources]")
        try:
            clock.parse_date(str(f.get("d")))
        except ValueError:
            out.append(f"{at}: date {f.get('d')!r} is not ISO")
        if f["s"] in READABLE and "v" not in f:
            out.append(f"{at}: status {f['s']} needs a value")
    return out


def validate(data: dict[str, dict[str, Any]], harness_ids: list[str] | None = None) -> list[str]:
    """Problems, one string each; empty means the tables are well formed."""
    problems: list[str] = []
    for name, doc in data.items():
        problems += _shape_problems(name, doc) + _fact_problems(name, doc)
    rows = data["context_harnesses.toml"].get("harness", {})
    for hid in harness_ids or []:
        if hid not in rows:
            problems.append(
                f"context_harnesses.toml: harness {hid!r} has no row (add one, even empty)"
            )
    unknown = family_row(data, UNKNOWN)
    if not isinstance(unknown, dict):
        problems.append("context_families.toml: family 'unknown' is missing or not a table")
    elif any(is_fact(v) for v in unknown.values()):
        problems.append(
            "context_families.toml: family 'unknown' must have no facts (no neighbour borrowing)"
        )
    return problems


def usable(fact: dict[str, Any] | None, model: str = "") -> Any:
    """The fact's value if it may be believed, else None.

    `only` scopes the fact to models whose name contains one of its substrings (no model
    name, or no match: not usable). `exc` then overrides `v` for the longest matching key."""
    if not is_fact(fact) or fact["s"] not in READABLE:
        return None
    if "only" in fact and not any(k and k in model for k in fact["only"]):
        return None
    hits = [k for k in fact.get("exc", {}) if k and k in model]
    return fact["exc"][max(hits, key=len)] if hits else fact.get("v")


def family_row(data: dict[str, dict[str, Any]], family: str) -> dict[str, Any]:
    """The row for a family id; any id not in the table resolves to `unknown`."""
    rows = data["context_families.toml"].get("family", {})
    return rows.get(family) or rows.get(UNKNOWN) or {}


def stale(
    data: dict[str, dict[str, Any]],
    max_age_days: int,
    today: date | None = None,
) -> list[str]:
    """`table.row.key (N days old)` for each fact older than `max_age_days`."""
    today = today or date.today()
    out = []
    for doc in data.values():
        for table, row, key, f in facts(doc):
            try:
                age = (today - clock.parse_date(str(f.get("d")))).days
            except ValueError:
                continue
            if age > max_age_days:
                out.append(f"{table}.{row}.{key} ({age} days old)")
    return out


def doctor_notes(max_age_days: int, today: date | None = None) -> list[str]:
    """One doctor note naming the stale facts, or none."""
    old = stale(load(), max_age_days, today)
    if not old:
        return []
    shown = ", ".join(old[:_SHOWN]) + (
        f" and {len(old) - _SHOWN} more" if len(old) > _SHOWN else ""
    )
    return [
        f"context facts older than {max_age_days} days: {shown} (re-read the source, update `d`)"
    ]
