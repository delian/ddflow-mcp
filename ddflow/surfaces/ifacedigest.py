"""The interface digest and the soft/hard classifier of a ddflow upgrade (SU-digest).

What a client can see and negotiate is the *interface*: the tools it lists (name,
description, input schema, annotations), the prompts (name, arguments), the instructions
template, the capabilities and protocol versions, and the shim and worker protocol. The
digest is a sha256 over a canonical JSON of exactly that, so two builds that expose the same
interface have the same digest, in any process, in any release (the release version is left
out on purpose: it changes on every upgrade).

`classify` applies the five-rung rule of D-soft-upgrade-rule to an old and a new snapshot:

1. digest unchanged -- soft, nothing to announce;
2. the client honours `list_changed` -- soft, announce;
3. the change is additive -- soft for every client (new tools appear after a restart for a
   client that ignores `list_changed`);
4. an existing tool's schema changed incompatibly -- soft only with schema grace (the worker
   accepts the previous arguments for one version; the caller says so, the default is that it
   does not);
5. HARD: a shim or protocol change, always and over every other rung; or an incompatible
   change to an existing tool or prompt for a client that does not honour `list_changed`,
   with no schema grace. A client of unknown capability is treated as one that ignores.

Facade mode (`ddflow_search` / `ddflow_call`) keeps the visible list stable: with it on, only
the facade tools are compared, and a change behind them is soft.

The facade tools do not exist yet (MTD-search-call), so `live_snapshot(facade=True)` is empty
today; the filter is written against their names so it holds the day they are registered.
Prompts stay in a facade snapshot: a client sees them whatever the tool list holds.

Pure functions over plain data; only `live_snapshot` touches a running `Server`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: Bumped when the canonical form changes, so a digest of one form is never compared to another.
FORM = 1
#: The shim<->worker protocol majors (SU-worker-proto owns them once it lands): a change of
#: either is a HARD upgrade whatever the rest of the interface did.
SHIM_PROTO = 1
WORKER_PROTO = 1
#: The two tools a facade-mode server lists (MTD-search-call).
FACADE_TOOLS = ("ddflow_search", "ddflow_call")
HONOURS, IGNORES, UNKNOWN = "honours", "ignores", "unknown"


# -- the snapshot and its digest ---------------------------------------------------------


def _tool(t: dict[str, Any]) -> dict[str, Any]:
    out = {
        "name": t["name"],
        "description": t.get("description", ""),
        "inputSchema": t.get("inputSchema", {}),
        "annotations": t.get("annotations") or {},
    }
    if t.get("outputSchema") is not None:
        out["outputSchema"] = t["outputSchema"]
    return out


def _prompt(p: dict[str, Any]) -> dict[str, Any]:
    args = [
        {"name": a["name"], "required": bool(a.get("required", False))}
        for a in p.get("arguments") or []
    ]
    return {"name": p["name"], "arguments": sorted(args, key=lambda a: a["name"])}


def snapshot(
    *,
    tools: Iterable[dict[str, Any]],
    prompts: Iterable[dict[str, Any]] = (),
    instructions_id: str = "",
    capabilities: dict[str, Any] | None = None,
    protocols: Iterable[str] = (),
    shim_proto: int = 1,
    worker_proto: int = 1,
) -> dict[str, Any]:
    """The canonical interface of one exposed surface (a profile, flat, or the facade)."""
    return {
        "form": FORM,
        "tools": sorted((_tool(t) for t in tools), key=lambda t: t["name"]),
        "prompts": sorted((_prompt(p) for p in prompts), key=lambda p: p["name"]),
        "instructions_id": instructions_id,
        "capabilities": capabilities or {},
        "protocols": sorted(protocols),
        "shim_proto": shim_proto,
        "worker_proto": worker_proto,
    }


def canonical(snap: dict[str, Any]) -> str:
    """Compact JSON with sorted keys: the bytes the digest is taken over."""
    return json.dumps(snap, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(snap: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(snap).encode("ascii")).hexdigest()


def live_snapshot(
    server: Any,
    *,
    facade: bool = False,
    shim_proto: int = SHIM_PROTO,
    worker_proto: int = WORKER_PROTO,
) -> dict[str, Any]:
    """The snapshot of a running `Server` for its tier (or, with `facade`, its facade tools).

    The instructions template id is the sha256 of the UNRENDERED template, so the
    per-session variables in the rendered text do not change the digest.
    """
    from ..services import prompts as P
    from . import mcp_protocol as proto

    tools = server._m_tools_list({"id": 0}, False)["result"]["tools"]
    if facade:
        tools = [t for t in tools if t["name"] in FACADE_TOOLS]
    prompts = server._m_prompts_list({"id": 0}, False)["result"]["prompts"]
    try:
        text = P.resolve("mcp_instructions", server.repo).text
    except Exception as exc:  # a broken override must not stop an upgrade being classified
        text = f"unreadable: {type(exc).__name__}: {exc}"
    return snapshot(
        tools=tools,
        prompts=prompts,
        instructions_id=hashlib.sha256(text.encode()).hexdigest()[:16],
        capabilities=proto.capabilities(),
        protocols=[*proto.SUPPORTED_PROTOCOLS, *proto.MODERN_PROTOCOLS],
        shim_proto=shim_proto,
        worker_proto=worker_proto,
    )


# -- schema compatibility ----------------------------------------------------------------


def _norm(schema: Any) -> dict[str, Any]:
    """A schema as a dict: `true` accepts anything, `false` accepts nothing."""
    if isinstance(schema, dict):
        return schema
    return {} if schema is True else {"enum": []}


def _types(schema: dict[str, Any]) -> set[str] | None:
    t = schema.get("type")
    if t is None:
        return None
    return set(t) if isinstance(t, list) else {t}


def _widens(old: set[str], new: set[str]) -> bool:
    """Every type `old` accepts is accepted by `new` (an integer is a number)."""
    return all(t in new or (t == "integer" and "number" in new) for t in old)


#: Validation keywords where a larger value is looser / a smaller value is looser.
_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
#: Keywords that restrict by value: any change (or addition) can reject a call.
_EXACT = ("pattern", "format", "multipleOf", "uniqueItems")
#: Understood by `schema_problems` (compared above or by recursion), or irrelevant to validity.
_KNOWN = {
    "type", "enum", "required", "properties", "items", "const", "additionalProperties",
    *_LOWER_BOUNDS, *_UPPER_BOUNDS, *_EXACT,
    "description", "title", "default", "examples", "$comment", "deprecated", "readOnly",
    "writeOnly", "$schema", "$id",
}  # fmt: skip


def _canon(value: Any) -> str:
    """One JSON value as canonical text: tells 1 from "1" and true from 1, ignores key order."""
    return json.dumps(value, sort_keys=True)


def _jset(values: list[Any]) -> set[str]:
    return {_canon(v) for v in values}


def _additional_problems(old: dict[str, Any], new: dict[str, Any], where: str) -> list[str]:
    """`additionalProperties`: absent, `true` and `{}` are the loosest, `false` the tightest, a
    schema in between."""
    was, now = old.get("additionalProperties", True), new.get("additionalProperties", True)
    if now is True or now in ({}, was) or was is False:
        return []
    if now is False:
        return [f"{where}: additionalProperties restricted"]
    base = was if isinstance(was, dict) else {}
    return [f"{where}: additionalProperties {p}" for p in schema_problems(base, now)]


def _constraint_problems(old: dict[str, Any], new: dict[str, Any], where: str) -> list[str]:
    out: list[str] = []
    if "enum" in new and ("enum" not in old or not _jset(old["enum"]) <= _jset(new["enum"])):
        out.append(f"{where}: enum narrowed")
    for key in _LOWER_BOUNDS:
        if key in new and (key not in old or new[key] > old[key]):
            out.append(f"{where}: {key} tightened")
    for key in _UPPER_BOUNDS:
        if key in new and (key not in old or new[key] < old[key]):
            out.append(f"{where}: {key} tightened")
    for key in _EXACT:
        if key in new and new[key] != old.get(key):
            if not (key == "uniqueItems" and new[key] is False):
                out.append(f"{where}: {key} changed")
    if "const" in new and ("const" not in old or _canon(new["const"]) != _canon(old["const"])):
        out.append(f"{where}: const changed")
    out += _additional_problems(old, new, where)
    for key in sorted((set(old) | set(new)) - _KNOWN):
        if old.get(key) != new.get(key):
            out.append(f"{where}: {key} changed (not understood: assumed to restrict)")
    return out


def schema_problems(old: dict[str, Any], new: dict[str, Any], path: str = "") -> list[str]:
    """Why a call valid under `old` could fail under `new`; empty when `new` accepts all of
    `old`'s calls. Looks at types, required and removed arguments, enums and value bounds,
    `items`, and a constraint the old schema did not have."""
    old, new = _norm(old), _norm(new)
    where = path or "arguments"
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if nt is not None and (ot is None or not _widens(ot, nt)):
        problems.append(f"{where}: type changed from {sorted(ot or [])} to {sorted(nt)}")
    problems += _constraint_problems(old, new, where)
    for name in sorted(set(new.get("required") or []) - set(old.get("required") or [])):
        problems.append(f"{where}: new required argument {name}")
    old_props, new_props = old.get("properties") or {}, new.get("properties") or {}
    for name in sorted(old_props):
        sub = f"{path}.{name}" if path else name
        if name not in new_props:
            problems.append(f"{sub}: argument removed")
        else:
            problems += schema_problems(old_props[name], new_props[name], sub)
    new_items = new.get("items")
    if new_items is False and old.get("items") is not False:
        problems.append(f"{where}[]: items restricted")
    elif isinstance(new_items, dict) and new_items:
        old_items = old.get("items") if isinstance(old.get("items"), dict) else {}
        problems += schema_problems(old_items, new_items, f"{where}[]")
    return problems


def output_problems(old: Any, new: Any, path: str = "") -> list[str]:
    """Why a client reading a result under the OLD output schema could break on the new one:
    the schema is gone, a field it read is gone or no longer required, or its type is no longer
    one the old schema allowed. A field the new schema adds is harmless."""
    if old is None:
        return []
    where = path or "result"
    if new is None:
        return [f"{where}: output schema removed"]
    old, new = _norm(old), _norm(new)
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if ot and (nt is None or not _widens(nt, ot)):
        problems.append(f"{where}: type changed from {sorted(ot)} to {sorted(nt or [])}")
    for name in sorted(set(old.get("required") or []) - set(new.get("required") or [])):
        problems.append(f"{where}: {name} is no longer guaranteed")
    if "const" in old and ("const" not in new or _canon(new["const"]) != _canon(old["const"])):
        problems.append(f"{where}: const is no longer guaranteed")
    if "enum" in old and ("enum" not in new or not _jset(new["enum"]) <= _jset(old["enum"])):
        problems.append(f"{where}: enum is no longer guaranteed")
    if isinstance(old.get("items"), (dict, bool)) and old["items"] != {}:
        problems += output_problems(old["items"], new.get("items") or {}, f"{where}[]")
    new_props = new.get("properties") or {}
    for name, sub in sorted((old.get("properties") or {}).items()):
        here = f"{path}.{name}" if path else name
        if name not in new_props:
            problems.append(f"{here}: field removed")
        else:
            problems += output_problems(sub, new_props[name], here)
    return problems


# -- classification ---------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    kind: str  # "soft" | "hard"
    rung: int  # 1..5, the rule that decided it
    reasons: tuple[str, ...]
    announce: bool = False  # send list_changed
    pending: tuple[
        str, ...
    ] = ()  # what a client that ignores list_changed sees only after a restart


def _exposed(snap: dict[str, Any], facade: bool) -> dict[str, Any]:
    if not facade:
        return snap
    return {**snap, "tools": [t for t in snap["tools"] if t["name"] in FACADE_TOOLS]}


def _by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in items}


def _prompt_problems(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    was = {a["name"]: a["required"] for a in old["arguments"]}
    out = []
    for a in new["arguments"]:
        if a["required"] and not was.get(a["name"], False):
            out.append(f"prompt {new['name']}: argument {a['name']} became required")
    for name in sorted(set(was) - {a["name"] for a in new["arguments"]}):
        out.append(f"prompt {new['name']}: argument {name} removed")
    return out


def _diff_meta(old: dict[str, Any], new: dict[str, Any], d: dict[str, list[str]]) -> None:
    """Protocol, capability and instruction changes."""
    for key, what in (("shim_proto", "shim"), ("worker_proto", "worker")):
        if old[key] != new[key]:
            d["hard_always"].append(f"{what} protocol changed {old[key]} -> {new[key]}")
    if old["capabilities"] != new["capabilities"]:
        d["hard_always"].append("capabilities changed (negotiated at initialize)")
    for v in sorted(set(old["protocols"]) - set(new["protocols"])):
        d["hard_always"].append(f"protocol version {v} no longer served")
    for v in sorted(set(new["protocols"]) - set(old["protocols"])):
        d["additive"].append(f"protocol version {v} added")
    if old["instructions_id"] != new["instructions_id"]:
        d["additive"].append("instructions apply to the next session")


def _diff_tools(old: dict[str, Any], new: dict[str, Any], d: dict[str, list[str]]) -> None:
    ot, nt = _by_name(old["tools"]), _by_name(new["tools"])
    d["incompatible"] += [f"tool {n} removed" for n in sorted(set(ot) - set(nt))]
    d["additive"] += [f"tool {n}" for n in sorted(set(nt) - set(ot))]
    for name in sorted(set(ot) & set(nt)):
        if ot[name] == nt[name]:
            continue
        problems = schema_problems(ot[name]["inputSchema"], nt[name]["inputSchema"])
        d["incompatible"] += [
            f"tool {name}: output {p}"
            for p in output_problems(ot[name].get("outputSchema"), nt[name].get("outputSchema"))
        ]
        if problems:
            d["schema"] += [f"tool {name}: {p}" for p in problems]
        else:
            d["additive"].append(f"tool {name}: description or schema extended")


def _diff_prompts(old: dict[str, Any], new: dict[str, Any], d: dict[str, list[str]]) -> None:
    op, np_ = _by_name(old["prompts"]), _by_name(new["prompts"])
    d["incompatible"] += [f"prompt {n} removed" for n in sorted(set(op) - set(np_))]
    d["additive"] += [f"prompt {n}" for n in sorted(set(np_) - set(op))]
    for name in sorted(set(op) & set(np_)):
        d["incompatible"] += _prompt_problems(op[name], np_[name])
        had = {a["name"] for a in op[name]["arguments"]}
        d["additive"] += [
            f"prompt {name}: argument {a['name']}"
            for a in np_[name]["arguments"]
            if a["name"] not in had and not a["required"]
        ]


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    """What changed, sorted: `hard_always`, `incompatible`, `schema`, `additive`."""
    d: dict[str, list[str]] = {"hard_always": [], "incompatible": [], "schema": [], "additive": []}
    _diff_meta(old, new, d)
    _diff_tools(old, new, d)
    _diff_prompts(old, new, d)
    return d


def classify(
    old: dict[str, Any],
    new: dict[str, Any],
    client: str = UNKNOWN,
    *,
    facade: bool = False,
    schema_grace: bool = False,
) -> Verdict:
    """Soft or hard, by the five rungs, for a client that `honours`, `ignores` or is of
    `unknown` capability (treated as ignoring: the safe reading)."""
    eo, en = _exposed(old, facade), _exposed(new, facade)
    d = _diff(eo, en)
    note: list[str] = []
    if facade and digest(eo) == digest(en) and digest(old) != digest(new):
        note = ["changes behind the facade are invisible to the client"]
    if d["hard_always"]:
        return Verdict("hard", 5, tuple(d["hard_always"]))
    if digest(eo) == digest(en):
        return Verdict("soft", 1, ("interface digest unchanged", *note))
    changes = [*d["incompatible"], *d["schema"], *d["additive"]]
    pending = tuple(sorted(c for c in d["additive"] if not c.startswith("protocol")))
    if client == HONOURS:
        return Verdict("soft", 2, ("client honours list_changed", *changes), True)
    if not d["incompatible"] and not d["schema"]:
        return Verdict("soft", 3, ("additive change", *d["additive"]), False, pending)
    reasons = (*d["incompatible"], *d["schema"])
    if schema_grace and not d["incompatible"]:
        return Verdict(
            "soft",
            4,
            ("schema grace: the worker accepts the previous arguments", *reasons),
            False,
            pending,
        )
    why = "no list_changed" if client == IGNORES else "client capability unknown"
    return Verdict("hard", 5, (f"incompatible change, {why}, no schema grace", *reasons))


_ORDER = {"soft": 0, "hard": 1}


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    """The hardest verdict (the first of equals)."""
    vs = list(verdicts)
    return max(vs, key=lambda v: (_ORDER[v.kind], v.rung)) if vs else Verdict("soft", 1, ())


def classify_surfaces(
    old: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
    client: str = UNKNOWN,
    **kw: Any,
) -> Verdict:
    """`classify` each named surface (flat, a profile, facade). The kind and rung are the worst
    surface's; the reasons and the pending list are every surface's, each prefixed with its
    name. A surface that vanished counts as hard, one that appeared as additive."""
    out: list[tuple[str, Verdict]] = []
    for name in sorted(set(old) | set(new)):
        if name not in new:
            out.append((name, Verdict("hard", 5, ("removed",))))
        elif name not in old:
            honours = client == HONOURS  # rung 2: tell it to list again
            pending = () if honours else ("surface",)
            out.append((name, Verdict("soft", 2 if honours else 3, ("added",), honours, pending)))
        else:
            out.append((name, classify(old[name], new[name], client, **kw)))
    top = worst(v for _, v in out)
    return Verdict(
        top.kind,
        top.rung,
        tuple(f"{n}: {r}" for n, v in out for r in v.reasons),
        any(v.announce for _, v in out),
        tuple(f"{n}: {p}" for n, v in out for p in v.pending),
    )
