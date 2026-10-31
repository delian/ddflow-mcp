"""The combination rule: `effective(harness, model, route)` (DRV-combine).

Pure and deterministic: no I/O, no clock unless `today` is passed, nothing read from disk.
Callers hand over the three layers as `key -> fact record` tables (`facts`) and get each
requested key back with the layer that decided it and why (research/driver-architecture.md
section 4.1).

Precedence of an ordinary key, strongest first: observation, operator, pair, route, model,
family, harness, lowest common denominator (`Policy.lcd`). The first layer holding a
believable fact wins; a fact that drifted (`factdrift`) or is `unverified` is skipped and
never beats a verified one below it, and the skip is said in the reason. Safety keys do not
use precedence: every `cap.<key>` tightens `<key>` by `min` and any layer's truthy
`prohibit.*` wins. Unknown never reads as supported: a missing fact yields the LCD value and
the field is marked `unknown`, naming the model when it matched no catalog row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import factdrift, facts, modelreg

#: Strongest first. `harness` is last of the data layers: mechanism, not numbers.
LAYERS = ("observation", "operator", "pair", "route", "model", "family", "harness")
LCD = "lcd"
UNKNOWN_FAMILY = modelreg.UNKNOWN


@dataclass(frozen=True)
class Policy:
    """Every tunable of the rule."""

    cap_prefix: str = "cap."
    prohibit_prefix: str = "prohibit."
    #: the value used, per key, when no layer knows (lowest common denominator, section 6)
    lcd: Mapping[str, Any] = field(
        default_factory=lambda: {
            "cache.explicit": False,
            "cache.stable_prefix": True,
            "long_context": False,
            "reasoning.knob": False,
        }
    )
    #: a safety fact (cap, prohibition) that is unverified or drifted still tightens
    safety_trusts_unverified: bool = True
    grace_days: int = 0


@dataclass(frozen=True)
class Field:
    key: str
    value: Any
    source: str  # a layer name, LCD, or `cap` / `prohibit` when a safety fact decided
    unknown: bool
    reason: str
    fact: dict[str, Any] | None = None


@dataclass(frozen=True)
class Effective:
    harness: str
    model: str
    family: str
    route: str
    fields: Mapping[str, Field]
    unknown_models: tuple[str, ...] = ()

    def __getitem__(self, key: str) -> Any:
        return self.fields[key].value

    def get(self, key: str, default: Any = None) -> Any:
        f = self.fields.get(key)
        return default if f is None else f.value

    def reasons(self) -> dict[str, str]:
        return {k: f"{f.source}: {f.reason}" for k, f in self.fields.items()}


def _layer_tables(
    harness: Mapping[str, Any],
    model: Mapping[str, Any],
    route: Mapping[str, Any] | None,
    pair: Mapping[str, Any] | None,
    operator: Mapping[str, Any] | None,
    observed: Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    return {
        "observation": observed or {},
        "operator": operator or {},
        "pair": pair or {},
        "route": (route or {}).get("facts", {}),
        "model": model.get("facts", {}),
        "family": model.get("family_facts", {}),
        "harness": harness.get("facts", {}),
    }


def match_pair(pairs: list[Mapping[str, Any]], harness: str, model: str, family: str) -> dict:
    """Facts of the first sparse `[[pair]]` row for this harness and model (a model id or
    its family); `{}` when none. No wildcard: a pair is a stated exception."""
    for row in pairs:
        if row.get("harness") == harness and row.get("model") in (model, family):
            return dict(row.get("facts", {}))
    return {}


def _believable(node: Any, installed: str | None, today: date | None, grace: int):
    """(fact or None, note): the fact if readable and undrifted, else why it is skipped."""
    if not facts.is_fact(node):
        return None, "not a fact record"
    a = factdrift.assess(node, installed, today, grace)
    if a.state != "ok":
        return None, f"{a.state} ({a.reason})"
    if node["s"] == "unverified":
        return None, "unverified"
    return node, ""


def _ordinary(
    key: str,
    tables: Mapping[str, Mapping[str, Any]],
    pol: Policy,
    installed: str | None,
    today: date | None,
    skips: list[str],
) -> Field | None:
    for layer in LAYERS:
        node = tables[layer].get(key)
        if node is None:
            continue
        fact, note = _believable(node, installed, today, pol.grace_days)
        if fact is None:
            skips.append(f"{layer} {note}")
            continue
        if fact["s"] == "none":
            return Field(key, None, layer, False, "documented absence", fact)
        extra = f" (skipped: {'; '.join(skips)})" if skips else ""
        return Field(key, fact.get("v"), layer, False, f"{fact['s']} fact{extra}", fact)
    return None


def _safety(
    prefix: str,
    key: str,
    tables: Mapping[str, Mapping[str, Any]],
    pol: Policy,
    installed: str | None,
    today: date | None,
) -> list[tuple[str, Any, dict[str, Any]]]:
    out = []
    for layer in LAYERS:
        node = tables[layer].get(prefix + key)
        if not facts.is_fact(node) or node["s"] == "none":
            continue
        fact, _ = _believable(node, installed, today, pol.grace_days)
        if fact is None and not pol.safety_trusts_unverified:
            continue
        out.append((layer, node.get("v"), node))
    return out


def _resolve_key(
    key: str,
    tables: Mapping[str, Mapping[str, Any]],
    pol: Policy,
    unknown_model: str,
    installed: str | None,
    today: date | None,
) -> Field:
    skips: list[str] = []
    prohibitions = [
        (layer, node)
        for layer, v, node in _safety(pol.prohibit_prefix, key, tables, pol, installed, today)
        if v
    ]
    if prohibitions:
        layer, node = prohibitions[0]
        names = ", ".join(layer_ for layer_, _ in prohibitions)
        return Field(key, False, "prohibit", False, f"prohibited by {names}", node)
    got = _ordinary(key, tables, pol, installed, today, skips)
    if got is None:
        who = f" for unknown model {unknown_model!r}" if unknown_model else ""
        why = f"; skipped: {'; '.join(skips)}" if skips else ""
        reason = f"not verified{who}, using lowest common denominator{why}"
        return Field(key, pol.lcd.get(key), LCD, True, reason)
    caps = [
        (layer, v, n)
        for layer, v, n in _safety(pol.cap_prefix, key, tables, pol, installed, today)
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if caps and isinstance(got.value, (int, float)) and not isinstance(got.value, bool):
        layer, low, node = min(caps, key=lambda c: c[1])
        if low < got.value:
            reason = f"capped to {low} by {layer}; {got.source} said {got.value}"
            return Field(key, low, "cap", False, reason, node)
    return got


def effective(
    harness: Mapping[str, Any],
    model: Mapping[str, Any] | str,
    route: Mapping[str, Any] | str | None = None,
    *,
    catalog: modelreg.Catalog | None = None,
    keys: list[str] | tuple[str, ...] = (),
    pair: Mapping[str, Any] | None = None,
    operator: Mapping[str, Any] | None = None,
    observed: Mapping[str, Any] | None = None,
    installed: str | None = None,
    today: date | None = None,
    policy: Policy | None = None,
) -> Effective:
    """Each of `keys` (default: every key any layer states, caps and prohibitions folded
    into their base key) resolved with its winning layer.

    `harness`, `model`, `route` are `{id, facts: {key: fact}}`; a model also carries
    `family` (a catalog family or `unknown`) and `family_facts`. A model (and route) may be
    a name with `catalog=`. `pair`, `operator` and `observed` are plain `key -> fact` tables (see `match_pair`)."""
    if isinstance(model, str):  # names, read from the catalog (`model_layer`)
        got = model_layer(
            catalog or modelreg.Catalog(), model, route if isinstance(route, str) else None
        )
        model, route = got["model"], got["route"]
    pol = policy or Policy()
    tables = _layer_tables(harness, model, route, pair, operator, observed)
    family = str(model.get("family") or UNKNOWN_FAMILY)
    unknown = str(model.get("id", "")) if family == UNKNOWN_FAMILY else ""
    wanted = list(keys) or _all_keys(tables, pol)
    fields = {
        k: _resolve_key(k, tables, pol, unknown, installed, today) for k in sorted(set(wanted))
    }
    return Effective(
        str(harness.get("id", "")),
        str(model.get("id", "")),
        family,
        str((route or {}).get("id", "")),
        fields,
        (unknown,) if unknown else (),
    )


def _all_keys(tables: Mapping[str, Mapping[str, Any]], pol: Policy) -> list[str]:
    out = set()
    for tbl in tables.values():
        for k in tbl:
            base = k
            for p in (pol.cap_prefix, pol.prohibit_prefix):
                if k.startswith(p):
                    base = k[len(p) :]
                    break
            out.add(base)
    return sorted(out)


def _route_facts(cat: modelreg.Catalog, res: modelreg.Resolved, route: str | None) -> dict:
    """Class facts, overlaid by the route's, overlaid by its served-model table."""
    rrow = cat.route.get(route or "", {}) if route else {}
    served = rrow.get("served") if isinstance(rrow.get("served"), dict) else {}
    srv = served.get(res.model) or served.get(res.row) or {}
    cname = rrow.get("class")
    cls = cat.cls.get(cname, {}) if isinstance(cname, str) else {}
    out: dict[str, Any] = {}
    for tbl in (cls, rrow, srv if isinstance(srv, dict) else {}):  # weakest first
        out.update({k: v for k, v in tbl.items() if facts.is_fact(v) and k != "endpoint"})
    return out


def _facts_of(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if facts.is_fact(v)}


def model_layer(cat: modelreg.Catalog, model: str, route: str | None = None) -> dict[str, Any]:
    """The `model` and `route` arguments of `effective` read from a loaded catalog:
    a model row's facts, its family row's, the route's (served table, route, class). A name
    that matches no row has family `unknown` and no facts, never a neighbour's."""
    res = modelreg.resolve(cat, model)
    row = cat.model.get(res.row, {}) if res.row else {}
    return {
        "model": {
            "id": model,
            "family": res.family,
            "facts": _facts_of(row),
            "family_facts": _facts_of(cat.family.get(res.family, {})),
        },
        "route": {"id": route or "", "facts": _route_facts(cat, res, route)},
    }
