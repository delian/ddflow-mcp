"""The model catalog: families, models and routes as fact records (DRV-model-catalog).

Data: `ddflow/models/{families,models,routes}.toml`, overlaid by the git-ignored
`.ddflow/local/models.toml` (same shape; the place for endpoint addresses, key env names and
what a local server serves, rule D-no-own-services-local-dir). Every number is the shared
fact record of `facts` (value, status, source, date); the three-valued rule holds: a value, a
documented absence, or left out. Nothing is guessed, and a model matching no row resolves to
the family `unknown`, never to a neighbour by name.

Precedence of a fact for `read(model, key, route)`, strongest first (research/driver-
architecture.md section 4): the route's served-model table, the route, the route class, the
model, the catalog family row, the cache-family table (`context_families`). The first tier with
a believable, undrifted fact answers; a miss says why. Caps only tighten is a company-layer
rule applied by callers, not here.

`family_hints()` and `presets()` rebuild the former `FAMILY_HINTS` and review `PRESETS`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infra import paths
from . import context_families as CF
from . import factdrift, facts

UNKNOWN = CF.UNKNOWN
LOCAL_FILE = "models.toml"
_TABLES = ("family", "model", "route", "class")
#: Route keys that are not facts about a model.
_ROUTE_META = ("endpoint",)
#: Route classes that need no [class] row (they carry no shared facts).
_PLAIN_CLASSES = ("saas", "command", "proxy")


def data_dir() -> Path:
    return paths.package_dir() / "models"


@dataclass
class Catalog:
    family: dict[str, dict[str, Any]] = field(default_factory=dict)
    model: dict[str, dict[str, Any]] = field(default_factory=dict)
    route: dict[str, dict[str, Any]] = field(default_factory=dict)
    cls: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    context: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ids that came from the local overlay (not committed)
    local_ids: set[str] = field(default_factory=set)
    #: rows that are not tables, kept out of the tables but reported by `validate`
    bad_rows: list[str] = field(default_factory=list)


def _read(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _merge_row(into: dict[str, Any], row: dict[str, Any]) -> None:
    """Overlay `row` on `into` fact by fact; `served` merges per served model too."""
    for key, node in row.items():
        if key == "served" and isinstance(node, dict) and isinstance(into.get(key), dict):
            for model, tbl in node.items():
                have = into[key].get(model)
                if isinstance(tbl, dict) and isinstance(have, dict):
                    have.update(tbl)
                else:  # malformed: keep it so `validate` reports it
                    into[key][model] = tbl
        else:
            into[key] = node


def load(
    data: Path | None = None, local_dir: Path | None = None, templates: Path | None = None
) -> Catalog:
    """Shipped files, then `local_dir/models.toml` when it exists: a local row of the same id
    replaces fact by fact, so a project can correct one number without restating the row."""
    cat = Catalog(context=CF.load(templates))
    base = data or data_dir()
    docs = [(_read(base / n), False) for n in ("families.toml", "models.toml", "routes.toml")]
    if local_dir is not None:
        docs.append((_read(local_dir / LOCAL_FILE), True))
    for doc, local in docs:
        cat.sources.update(doc.get("sources", {}))
        for table, dest in zip(_TABLES, (cat.family, cat.model, cat.route, cat.cls), strict=True):
            for rid, row in (doc.get(table) or {}).items():
                if not isinstance(row, dict):
                    cat.bad_rows.append(f"{table}.{rid} is not a table")
                else:
                    _merge_row(dest.setdefault(rid, {}), row)
                    if local:
                        cat.local_ids.add(f"{table}.{rid}")
    return cat


def _rows(cat: Catalog) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (t, rid, row)
        for t, tbl in zip(_TABLES, (cat.family, cat.model, cat.route, cat.cls), strict=True)
        for rid, row in tbl.items()
    ]


def _fact_nodes(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for key, node in row.items():
        if facts.is_fact(node):
            out.append((key, node))
        elif key == "served" and isinstance(node, dict):
            for served, tbl in node.items():
                if not isinstance(tbl, dict):
                    continue  # `_shape_problems` names it
                out += [(f"served.{served}.{k}", n) for k, n in tbl.items() if facts.is_fact(n)]
    return out


#: Row keys that are plain data, not facts.
_PLAIN_KEYS = ("note", "family", "class", "served")


def _row_problem(at: str, key: str, node: Any) -> str:
    """What is wrong with one row value, or empty."""
    if key in ("family", "class"):
        return "" if isinstance(node, str) else f"{at} must be a string"
    if key == "served":
        ok = isinstance(node, dict) and all(isinstance(v, dict) for v in node.values())
        if not ok:
            return f"{at} must be a table of tables (served model -> facts)"
        loose = [
            f"{m}.{k}" for m, tbl in node.items() for k, v in tbl.items() if not facts.is_fact(v)
        ]
        return f"{at}.{loose[0]} is not a fact (no `s`) and would be ignored" if loose else ""
    if key not in _PLAIN_KEYS and not facts.is_fact(node):
        return f"{at} is not a fact (no `s`) and would be ignored"
    return ""


def _identity_problem(fid: str, row: dict[str, Any]) -> str:
    v = _identity(row)
    needles = v.get("needles")
    if not facts.is_fact(row.get("identity")):
        return ""
    ok = isinstance(v.get("vendor"), str) and v["vendor"] and isinstance(needles, list)
    if ok and all(isinstance(n, str) and n for n in needles):
        return ""
    return f"family.{fid}.identity: v needs a vendor string and a needles list"


def _shape_problems(cat: Catalog) -> list[str]:
    out = [
        _row_problem(f"{table}.{rid}.{key}", key, node)
        for table, rid, row in _rows(cat)
        for key, node in row.items()
    ]
    out += [_identity_problem(fid, row) for fid, row in cat.family.items()]
    return [p for p in out if p]


def _link_problems(cat: Catalog) -> list[str]:
    out = []
    for rid, row in cat.model.items():
        if not isinstance(row.get("family"), str) or row["family"] not in cat.family:
            out.append(f"model.{rid}: family {row.get('family')!r} is not in the catalog")
    for rid, row in cat.route.items():
        klass = row.get("class")
        if (
            isinstance(klass, str)
            and klass
            and klass not in cat.cls
            and klass not in _PLAIN_CLASSES
        ):
            out.append(f"route.{rid}: class {klass!r} has no [class] row")
    return out


def _family_problems(cat: Catalog) -> list[str]:
    out = []
    for fid, row in cat.family.items():
        if fid == UNKNOWN:
            if any(facts.is_fact(v) for v in row.values()) or "needles" in row:
                out.append("family.unknown must have no facts or needles (no neighbour)")
        elif fid != "local" and not facts.is_fact(row.get("identity")):
            out.append(f"family.{fid}: no `identity` fact (vendor, needles with source and date)")
    if UNKNOWN not in cat.family:
        out.append("family.unknown is missing")
    return out


def validate(cat: Catalog) -> list[str]:
    """Problems, one string each; empty means the catalog is well formed."""
    out = list(cat.bad_rows)
    for table, rid, row in _rows(cat):
        for key, node in _fact_nodes(row):
            at = f"{table}.{rid}.{key}"
            out += facts.problems(at, node, cat.sources)
            if node["s"] in facts.READABLE and "v" not in node:
                out.append(f"{at}: status {node['s']} needs a value")
    return out + _shape_problems(cat) + _link_problems(cat) + _family_problems(cat)


def _identity(row: dict[str, Any]) -> dict[str, Any]:
    ident = row.get("identity")
    v = ident.get("v") if facts.is_fact(ident) else None
    return v if isinstance(v, dict) else {}


def family_hints(cat: Catalog) -> dict[str, str]:
    """Needle -> vendor in catalog order: the former `config_sections.agent.FAMILY_HINTS`."""
    out: dict[str, str] = {}
    for row in cat.family.values():
        ident = _identity(row)
        for needle in ident.get("needles", []):
            out.setdefault(needle, ident.get("vendor", ""))
    return out


def presets(cat: Catalog) -> dict[str, dict[str, Any]]:
    """Route id -> endpoint settings: the former `review.PRESETS`. Provenance is on the route
    (`show_route`); a status of `unverified` does not hide the address, it is a convenience."""
    return {
        rid: dict(row["endpoint"]["v"])
        for rid, row in cat.route.items()
        if facts.is_fact(row.get("endpoint")) and isinstance(row["endpoint"].get("v"), dict)
    }


@dataclass(frozen=True)
class Resolved:
    model: str
    family: str  # a catalog family id, or `unknown`
    vendor: str  # pretraining lineage, "" when unknown
    how: str  # model | needle | unknown
    row: str = ""  # the model row id when listed


def resolve(cat: Catalog, model: str) -> Resolved:
    """A model name to its family: a listed model id (case-blind, exact), else the first
    family whose needle the name contains, else `unknown`. Never a nearest neighbour."""
    low = (model or "").strip().lower()
    if low:
        for rid, row in cat.model.items():
            if rid.lower() == low:
                fam = row.get("family")
                if (
                    not isinstance(fam, str) or fam not in cat.family
                ):  # a broken row is unknown, not a needle neighbour
                    return Resolved(model, UNKNOWN, "", "unknown")
                return Resolved(
                    model, fam, _identity(cat.family[fam]).get("vendor", ""), "model", rid
                )
        for fid, frow in cat.family.items():
            ident = _identity(frow)
            if any(n and n.lower() in low for n in ident.get("needles", [])):
                return Resolved(model, fid, ident.get("vendor", ""), "needle")
    return Resolved(model, UNKNOWN, "", "unknown")


def _tier(row: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    node = (row or {}).get(key)
    return node if facts.is_fact(node) else None


def _tiers(
    cat: Catalog, res: Resolved, route: str | None
) -> list[tuple[str, dict[str, Any] | None]]:
    r = cat.route.get(route or "", {}) if route else {}
    table = r.get("served") if isinstance(r.get("served"), dict) else {}
    served = table.get(res.model) or table.get(res.row) or {}
    served = served if isinstance(served, dict) else {}
    return [
        ("route.served", served),
        ("route", {k: v for k, v in r.items() if k not in _ROUTE_META}),
        ("class", cat.cls.get(r["class"], {}) if isinstance(r.get("class"), str) else {}),
        ("model", cat.model.get(res.row, {}) if res.row else {}),
        ("family", cat.family.get(res.family, {})),
        ("cache-family", None),
    ]


def read(
    cat: Catalog,
    model: str,
    key: str,
    route: str | None = None,
    installed: str | None = None,
    **kw: Any,
) -> factdrift.Pick:
    """The believable value of `key` for `model` (through `route`), with its fact, or None
    and a reason. Documented absence returns value None with the fact and reason `absent`."""
    res = resolve(cat, model)
    leads = []
    for name, row in _tiers(cat, res, route):
        if name == "cache-family":
            node = (
                CF.family_row(cat.context, res.family).get(key) if res.family != UNKNOWN else None
            )
            node = node if facts.is_fact(node) else None
            if node is not None and node["s"] in facts.READABLE:
                val = CF.usable(node, model)
                if val is None:
                    leads.append(f"cache-family: {key} is scoped to other models")
                node = None if val is None else {**node, "v": val}
        else:
            node = _tier(row, key)
        if node is None:
            continue
        got = factdrift.pick([node], None, installed, key, **kw)
        if got.fact is not None:
            reason = "absent: documented not to exist" if node["s"] == "none" else ""
            return factdrift.Pick(got.value, got.fact, reason or f"{name}")
        leads.append(f"{name}: {got.reason}")
    why = "; ".join(leads) or "no fact recorded"
    return factdrift.Pick(None, None, f"{key} not verified for {model or '(no model)'} ({why})")


def stale(cat: Catalog, max_age_days: int, today: Any = None) -> list[str]:
    """Facts older than `max_age_days`, as `table.row.key (N days old)`."""
    from datetime import date

    from ..core import clock

    today = today or date.today()
    out = []
    for table, rid, row in _rows(cat):
        for key, f in _fact_nodes(row):
            try:
                age = (today - clock.parse_date(str(f.get("d")))).days
            except ValueError:
                continue
            if age > max_age_days:
                out.append(f"{table}.{rid}.{key} ({age} days old)")
    return out
