"""The feature catalog loader: in-agent and orchestrator features as data.

The data lives in ``ddflow/harnesses/catalog.toml``; this module reads and validates it.
A row names the descriptor field(s) that answer a feature and the offline conformance
check(s) that grade it. Both are resolved against the LIVE schema and check modules, so the
catalog stays in sync with the descriptor and never reimplements a check:

* ``facts`` / ``hooks``        -- ``ddflow.services.driver_conformance.run`` keys
* ``hook:<event>`` / ``argv`` / ``stream`` -- ``tests/harness_conformance.declared`` kinds
* ``lane``                     -- ``ddflow.services.swarm.conformance`` levels 1-7

Any drift (an unknown field, an invented check, a duplicate slug, a malformed history
entry) raises :class:`CatalogError` naming the row. Adding a feature is editing the TOML
data file and its ``[[history]]`` ledger, not new code.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path
from typing import Any

from ddflow.services import harnessreg as HR
from ddflow.services import harnessv2 as HV
from ddflow.services.swarm import conformance as CF

IN_AGENT = "in_agent"
ORCHESTRATOR = "orchestrator"
AXES = (IN_AGENT, ORCHESTRATOR)

HISTORY_ACTIONS = ("add", "edit", "retire")

#: Check ids that resolve to ``driver_conformance.run``'s emitted keys.
FIXED_CHECKS = ("facts", "hooks")
#: Declared fixture kinds that are not per-event (``hook`` is, so it is spelled ``hook:<e>``).
DECLARED_CHECKS = ("argv", "stream")
#: The lane conformance levels 1-7, a single cited check.
LANE_CHECK = "lane"

_CATALOG = Path(__file__).resolve().parents[2] / "harnesses" / "catalog.toml"


class CatalogError(ValueError):
    """A catalog row that is malformed; the message names the file and row."""


def _sections() -> tuple[tuple[str, type], ...]:
    """The descriptor's typed sections, read from the live dataclasses (the schema owner)."""
    return (
        ("mcp", HR.Mcp),
        ("instructions", HR.Instructions),
        ("hooks", HR.Hooks),
        ("plugin", HR.Plugin),
        ("commands", HR.Commands),
        ("skills", HR.Skills),
        ("subagents", HR.Subagents),
        ("env", HR.Env),
        ("worktrees", HR.Worktrees),
        ("transcripts", HR.Transcripts),
        ("verified", HR.Verified),
        ("detect", HV.Detect),
        ("headless", HV.Headless),
        ("continuation", HV.Continuation),
        ("orchestration", HV.Orchestration),
        ("meters", HV.Meters),
        ("caps", HV.Caps),
        ("autonomy", HV.Autonomy),
        ("telemetry", HV.Telemetry),
    )


def field_paths() -> frozenset[str]:
    """Every ``section.key`` the descriptor can name, plus the reserved section names.

    Computed from the live dataclasses, so the loader asserts against the schema itself.
    """
    out: set[str] = {"id", "rank", "family", "title", "delta"}
    for name, cls in _sections():
        out.add(name)
        out.update(f"{name}.{f.name}" for f in dc_fields(cls))
    out.update(HV.RESERVED_SECTIONS)
    return frozenset(out)


def known_check(cid: str) -> bool:
    """Whether ``cid`` resolves to an already-implemented offline check."""
    if cid in FIXED_CHECKS or cid in DECLARED_CHECKS or cid == LANE_CHECK:
        return True
    if cid.startswith("hook:"):
        return cid.partition(":")[2] in HR.CANONICAL_EVENTS
    return False


@dataclass(frozen=True)
class Row:
    feature: str
    axis: str
    title: str
    descriptor_fields: tuple[str, ...]
    checks: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class HistoryEntry:
    feature: str
    action: str
    date: str
    note: str = ""


@dataclass(frozen=True)
class Catalog:
    rows: tuple[Row, ...]
    history: tuple[HistoryEntry, ...]

    def by_axis(self, axis: str) -> tuple[Row, ...]:
        return tuple(r for r in self.rows if r.axis == axis)

    def by_feature(self, feature: str) -> Row:
        for r in self.rows:
            if r.feature == feature:
                return r
        raise KeyError(feature)


def _require(cond: bool, where: str, msg: str) -> None:
    if not cond:
        raise CatalogError(f"{where}: {msg}")


def _parse_row(raw: dict[str, Any], axis: str, where: str) -> Row:
    feature = raw.get("feature")
    title = raw.get("title", feature)
    fields_ = raw.get("descriptor_fields", [])
    checks = raw.get("checks", [])
    note = raw.get("note", "")
    _require(isinstance(feature, str) and feature, where, "a row needs a non-empty `feature` slug")
    _require(isinstance(fields_, list), where, "descriptor_fields must be a list")
    _require(isinstance(checks, list), where, "checks must be a list")
    _require(all(isinstance(f, str) for f in fields_), where, "descriptor_fields must be strings")
    _require(all(isinstance(c, str) for c in checks), where, "checks must be strings")
    _require(
        bool(fields_) or bool(checks) or bool(note),
        where,
        f"row {feature!r} names no descriptor field, no check and no note (a documented gap "
        "at least says where the knob lives)",
    )
    _require(
        bool(checks) or not fields_,
        where,
        f"row {feature!r} names descriptor field(s) but no offline check grades them",
    )
    paths = field_paths()
    for path in fields_:
        _require(path in paths, where, f"unknown descriptor field {path!r} (feature {feature!r})")
    for cid in checks:
        _require(known_check(cid), where, f"unknown check {cid!r} (feature {feature!r})")
    return Row(
        feature=feature,
        axis=axis,
        title=title,
        descriptor_fields=tuple(fields_),
        checks=tuple(checks),
        note=note,
    )


def load(path: str | Path | None = None) -> Catalog:
    """Load and validate the catalog data file (default ``ddflow/harnesses/catalog.toml``)."""
    p = Path(path) if path is not None else _CATALOG
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CatalogError(f"{p}: catalog data file not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{p}: {exc}") from None

    rows: list[Row] = []
    seen: set[str] = set()
    unknown_top = set(raw) - {"version", IN_AGENT, ORCHESTRATOR, "history"}
    _require(
        not unknown_top,
        p.name,
        f"unknown top-level key(s): {', '.join(sorted(unknown_top))} "
        "(expected version, [in_agent], [orchestrator], [history])",
    )
    for axis in AXES:
        section = raw.get(axis, [])
        _require(isinstance(section, list), p.name, f"[{axis}] must be an array of tables")
        for i, entry in enumerate(section):
            _require(isinstance(entry, dict), p.name, f"[{axis}][{i}] must be a table")
            row = _parse_row(entry, axis, f"{p.name}[{axis}][{i}]")
            _require(row.feature not in seen, row.feature, f"duplicate feature slug {row.feature!r}")
            seen.add(row.feature)
            rows.append(row)

    history: list[HistoryEntry] = []
    hraw = raw.get("history", [])
    _require(isinstance(hraw, list), p.name, "[history] must be an array of tables")
    for i, entry in enumerate(hraw):
        _require(isinstance(entry, dict), p.name, f"[history][{i}] must be a table")
        feature = entry.get("feature")
        action = entry.get("action")
        date = entry.get("date", "")
        note = entry.get("note", "")
        _require(isinstance(feature, str) and feature, p.name, "a history entry needs `feature`")
        _require(
            action in HISTORY_ACTIONS, p.name, f"unknown history action {action!r} for {feature!r}"
        )
        _require(isinstance(date, str) and date, p.name, f"history for {feature!r} needs a date")
        history.append(HistoryEntry(feature=feature, action=action, date=date, note=note))

    # A managed record's ledger is tied to its rows BOTH ways: a live row's latest action is
    # "add" or "edit" (an edit keeps it live), and a history slug whose latest action is not
    # "retire" must be a live row (an add/edit on a slug with no row is an orphan).
    latest_action: dict[str, str] = {}
    for entry in history:
        latest_action[entry.feature] = entry.action
    live = {row.feature for row in rows}
    for row in rows:
        _require(
            latest_action.get(row.feature) in ("add", "edit"),
            row.feature,
            f"live row needs an `add`/`edit` history entry (latest action: "
            f"{latest_action.get(row.feature)!r})",
        )
    for slug, action in latest_action.items():
        _require(
            action == "retire" or slug in live,
            slug,
            f"a history entry's latest action {action!r} needs a live row (or `retire`)",
        )

    return Catalog(rows=tuple(rows), history=tuple(history))
