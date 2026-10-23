"""Next-step hints for state-changing MCP results (MTD-next-hints).

An optional ``next`` field: at most `MAX_HINTS` hints and `MAX_BYTES` bytes, each
``{"tool", "why", "args_hint"}``. Rules (docs/ddflow/research/mcp-tool-diet.md section 6):

* derived only from the result the tool already computed (and the call's own arguments), never
  from a second query; a result with no state to read gets no hint;
* ``why`` is a fixed template, never text from a record (a record title is an instruction
  channel); ``args_hint`` carries ids and enum values only;
* ``tool`` names a tool the connection LISTS: on a family profile a hint is rewritten to the
  family action (``ddflow_gate`` with ``action=record``), and one the profile does not list is
  dropped; on a flat tier it is the flat name or dropped;
* ``merge`` is hinted only when ``gate status`` says the gates are complete.

The hints are advisory: the tool they name still checks the world (it does: claim refuses).
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config
from .tools import families as _families
from .tools import profiles as _profiles
from .tools import tiers as _tiers

MAX_HINTS = 3
MAX_BYTES = 400
MAX_WHY = 80

#: Gates whose outcome a reviewer gives, and gates a command gives.
_REVIEWED = ("rubber_duck", "critic")
_RUN = ("ci", "unit_tests", "bug_hunt")


def limit(value: str) -> int:
    """The max number of hints for a `[mcp].next_hints` value: ``off`` (default, and anything
    unreadable) none, ``on`` the cap, ``1``..``3`` that many."""
    text = (value or "").strip().lower()
    if text == "on":
        return MAX_HINTS
    return min(int(text), MAX_HINTS) if text.isdigit() else 0


def _hint(tool: str, why: str, **args: str) -> dict[str, Any]:
    return {"tool": tool, "why": why[:MAX_WHY], "args_hint": {k: v for k, v in args.items() if v}}


def _gate_status(item: str) -> dict[str, Any]:
    return _hint("ddflow_gate_status", "see the first open gate", id=item)


def _from_claim(args: dict, data: dict) -> list[dict]:
    item = str(data.get("item") or args.get("id") or "")
    return [_gate_status(item)] if item else []


def _from_gate_result(args: dict, data: dict) -> list[dict]:
    item = str(args.get("id") or "")
    return [_hint("ddflow_gate_status", "see the next open gate", id=item)] if item else []


def _from_gate_status(args: dict, data: dict) -> list[dict]:
    state = data["status"] if isinstance(data.get("status"), dict) else data  # nested in an outcome
    item = str(data.get("item") or data.get("id") or state.get("item") or args.get("id") or "")
    data = state
    if not item or "complete" not in data:
        return []
    if data.get("complete"):
        return [_hint("ddflow_merge", "every gate carries an outcome", id=item)]
    gate = str(data.get("current") or "")
    if not gate:
        return []
    if gate in _REVIEWED:
        return [_hint("ddflow_review", "this gate wants a reviewer", id=item, gate=gate)]
    if gate in _RUN:
        return [_hint("ddflow_gate_run", "this gate runs a command", id=item, gate=gate)]
    return [_hint("ddflow_gate_record", "record this gate's outcome", id=item, gate=gate)]


def _from_merge(args: dict, data: dict) -> list[dict]:
    item = str(data.get("id") or args.get("id") or "")
    return [_hint("ddflow_complete", "the branch is landed", id=item)] if item else []


def _from_complete(args: dict, data: dict) -> list[dict]:
    return [_hint("ddflow_next", "pick the next item")]


def _from_bug_found(args: dict, data: dict) -> list[dict]:
    fix = str(data.get("fix_task") or "")
    if data.get("fix_task_filed") and fix:
        return [_hint("ddflow_claim", "claim its fix task", id=fix)]
    return []


def _from_next(args: dict, data: dict) -> list[dict]:
    ready = data.get("ready") or []
    item = _id_of(ready[0]) if ready else ""
    return [_hint("ddflow_claim", "claim the first ready item", id=item)] if item else []


def _id_of(entry: Any) -> str:
    return str(entry.get("id", "")) if isinstance(entry, dict) else str(entry or "")


def _from_brief(args: dict, data: dict) -> list[dict]:
    """Work this agent holds first (its gate status), else the first ready item."""
    held = next((i for i in (_id_of(e) for e in data.get("held") or []) if i), "")
    if held:
        return [_gate_status(held)]
    ready = next((i for i in (_id_of(e) for e in data.get("ready") or []) if i), "")
    return [_hint("ddflow_claim", "claim the first ready item", id=ready)] if ready else []


_EMITTERS = {
    "ddflow_brief": _from_brief,
    "ddflow_claim": _from_claim,
    "ddflow_gate_record": _from_gate_result,
    "ddflow_gate_skip": _from_gate_result,
    "ddflow_gate_run": _from_gate_result,
    "ddflow_review": _from_gate_result,
    "ddflow_gate_status": _from_gate_status,
    "ddflow_merge": _from_merge,
    "ddflow_complete": _from_complete,
    "ddflow_bug_found": _from_bug_found,
    "ddflow_next": _from_next,
}


def _listed(hint: dict[str, Any], profile: str) -> dict[str, Any] | None:
    """``hint`` as the connection can follow it, or None. ``tool`` is a flat name here."""
    flat = hint["tool"]
    if profile in _profiles.PROFILES:
        if flat in _profiles.direct_tools(profile):
            return hint
        family, action = _families.placement(flat)
        if family in _profiles.family_tools(profile) and action:
            return {
                **hint,
                "tool": family,
                "args_hint": {"action": action, **hint["args_hint"]},
            }
        return None
    return hint if flat in _tiers.tier_tools(profile) else None


def fit(hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``hints`` cut to the caps: the first `MAX_HINTS`, then fewer until `MAX_BYTES` holds (the
    list is ordered by likelihood, so the last goes first)."""
    kept = hints[:MAX_HINTS]
    while kept and len(json.dumps(kept, separators=(",", ":")).encode()) > MAX_BYTES:
        kept = kept[:-1]
    return kept


def hints(
    tool: str, args: dict[str, Any], data: Any, profile: str, *, limit: int = MAX_HINTS
) -> list[dict[str, Any]]:
    """The ``next`` hints for a call of flat tool ``tool`` with ``args`` that answered ``data``,
    on a connection running ``profile`` (a flat tier or a profile); at most ``limit``."""
    emit = _EMITTERS.get(tool)
    if emit is None or not isinstance(data, dict) or limit <= 0:
        return []
    found = (_listed(h, profile) for h in emit(args if isinstance(args, dict) else {}, data))
    return fit([h for h in found if h is not None])[:limit]


def resolve(repo: Any) -> int:
    """`[mcp].next_hints` of ``repo`` (env `DDFLOW_MCP_NEXT_HINTS` wins) as a max, read once; a
    config that cannot be read is off."""
    try:
        return limit(Config.load(repo).mcp.next_hints)
    except Exception:
        return 0
