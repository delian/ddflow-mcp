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
   accepts the previous arguments for one version);
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
    if t.get("outputSchema"):
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


def live_snapshot(server: Any, *, facade: bool = False) -> dict[str, Any]:
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
    except Exception:  # a broken override must not stop an upgrade being classified
        text = "unreadable"
    return snapshot(
        tools=tools,
        prompts=prompts,
        instructions_id=hashlib.sha256(text.encode()).hexdigest()[:16],
        capabilities=proto.capabilities(),
        protocols=[*proto.SUPPORTED_PROTOCOLS, *proto.MODERN_PROTOCOLS],
    )


# -- schema compatibility ----------------------------------------------------------------


def _types(schema: dict[str, Any]) -> set[str] | None:
    t = schema.get("type")
    if t is None:
        return None
    return set(t) if isinstance(t, list) else {t}


def _widens(old: set[str], new: set[str]) -> bool:
    """Every type `old` accepts is accepted by `new` (an integer is a number)."""
    return all(t in new or (t == "integer" and "number" in new) for t in old)


def schema_problems(old: dict[str, Any], new: dict[str, Any], path: str = "") -> list[str]:
    """Why a call valid under `old` could fail under `new`; empty when `new` accepts all of
    `old`'s calls. Looks at types, required arguments, removed arguments, enums, items."""
    where = path or "arguments"
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if nt and (ot is None or not _widens(ot, nt)):
        problems.append(f"{where}: type changed from {sorted(ot or [])} to {sorted(nt)}")
    if "enum" in new and (
        "enum" not in old or not set(map(str, old["enum"])) <= set(map(str, new["enum"]))
    ):
        problems.append(f"{where}: enum narrowed")
    for name in sorted(set(new.get("required") or []) - set(old.get("required") or [])):
        problems.append(f"{where}: new required argument {name}")
    old_props, new_props = old.get("properties") or {}, new.get("properties") or {}
    for name in sorted(old_props):
        sub = f"{path}.{name}" if path else name
        if name not in new_props:
            problems.append(f"{sub}: argument removed")
        else:
            problems += schema_problems(old_props[name], new_props[name], sub)
    if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
        problems += schema_problems(old["items"], new["items"], f"{where}[]")
    return problems


def output_problems(
    old: dict[str, Any] | None, new: dict[str, Any] | None, path: str = ""
) -> list[str]:
    """Why a client reading a result under the OLD output schema could break on the new one:
    a field it read is gone, or its type is no longer one the old schema allowed. A field the
    new schema adds is harmless."""
    if not old or not new:
        return []
    where = path or "result"
    problems: list[str] = []
    ot, nt = _types(old), _types(new)
    if ot and nt and not _widens(nt, ot):
        problems.append(f"{where}: type changed from {sorted(ot)} to {sorted(nt)}")
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
        problems += [
            f"output {p}"
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
    schema_grace: bool = True,
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
    """`classify` each named surface (flat, a profile, facade) and take the worst; a surface
    that vanished or appeared counts as hard / additive."""
    out: list[Verdict] = []
    for name in sorted(set(old) | set(new)):
        if name not in new:
            out.append(Verdict("hard", 5, (f"surface {name}: removed",)))
        elif name not in old:
            honours = client == HONOURS  # rung 2: tell it to list again
            out.append(Verdict("soft", 2 if honours else 3, (f"surface {name}: added",), honours))
        else:
            v = classify(old[name], new[name], client, **kw)
            out.append(
                Verdict(
                    v.kind, v.rung, tuple(f"{name}: {r}" for r in v.reasons), v.announce, v.pending
                )
            )
    return worst(out)
