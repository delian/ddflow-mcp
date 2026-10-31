"""Small readers and the plan/doctor judgement helpers the steps share."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...infra import proc as P
from ._model import HAND_EDIT
from ._read import folded_state, state_digests, users_part


def tools_problems(tools: list[dict[str, Any]], budget: int) -> tuple[int, list[str]]:
    """`(compact bytes of tools/list, what is wrong with it)`: empty or carrying output schemas
    (`[mcp].output_schemas` ships off). ``budget`` is reported, never enforced: the whole list
    over `TOOLS_LIST_BUDGET` is a number, and only the core tier keeps a ratchet (D-mcp-budget-report)."""
    size = len(json.dumps({"tools": tools}, separators=(",", ":")))
    out = []
    if not tools:
        out.append("tools/list answered nothing")
    if any("outputSchema" in t for t in tools):
        out.append("tools/list carries output schemas ([mcp].output_schemas should be off)")
    return size, out


#: The opening of every MCP session the rehearsal holds.
_HELLO = (
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dry-run", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
)
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _call(at: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": at,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


def _items(plan: P.Captured) -> list[dict[str, Any]]:
    """Every item of a plan (`upgrade --plan --json`); none when it is not one."""
    try:
        cats = json.loads(plan.stdout)["categories"]
    except (ValueError, KeyError, TypeError):
        return []
    return [i for v in cats.values() for i in v]


def problem_identity(line: str) -> str:
    """What names a doctor problem across runs: its text up to the first em dash (so the class
    and the item, not the counts and paths that follow)."""
    return " ".join(line.split(" \u2014 ", maxsplit=1)[0].split())[:200]


def doctor_problems(done: P.Captured) -> tuple[str, ...] | None:
    """The problem identities of ``doctor --json``; None when doctor crashed (an exit other
    than 0 or 1), printed no report, or failed with no problem to show for it."""
    if not done.ran or done.returncode not in (0, 1):
        return None
    try:
        found = json.loads(done.stdout)["problems"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(found, list) or (done.returncode == 1 and not found):
        return None
    return tuple(problem_identity(str(x)) for x in found)


def new_problems(
    before: set[str], after: Iterable[str], contested: Iterable[str] = ()
) -> list[str]:
    """The problems in ``after`` that ``before`` did not have, less those that only name a
    record the plan announced as CONTESTED."""
    planned = set(contested)

    def announced(problem: str) -> bool:
        named = re.search(r"\b(decision|lesson|research) (\S+) is CONTESTED", problem)
        return bool(named) and f"{named.group(1)} {named.group(2)}" in planned

    return [p for p in sorted(set(after) - before) if not announced(p)]


def _asked(plan: P.Captured) -> list[dict[str, Any]]:
    """The items of a plan the operator would be asked to confirm."""
    return [i for i in _items(plan) if i["action"] == "needs operator confirmation"]


def _edit_kept(repo: Path, name: str) -> bool:
    """Is the rehearsal's hand edit still in ``name``, or kept beside it in a `.local-edits`?"""
    here = Path(repo) / name
    held = [here, *sorted(here.parent.glob(name + ".local-edits*"))]
    return any(HAND_EDIT in p.read_text("utf-8", "replace") for p in held if p.is_file())


def _key(item: dict[str, Any]) -> str:
    return str(
        item.get("key")
        or item.get("repair")
        or item.get("migration")
        or item.get("path")
        or item["id"]
    )


def _tool_body(result: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return dict(json.loads(((result or {}).get("content") or [{}])[0].get("text") or "{}"))
    except (ValueError, TypeError):
        return {}


def _pipeline(shown: P.Captured) -> list[str]:
    """The gates of an item's pipeline that are skipped by hand (`merge` is done by ddflow)."""
    try:
        return [g for g in json.loads(shown.stdout)["pipeline"] if g != "merge"]
    except (ValueError, KeyError, TypeError):
        return []


def _first_phase(repo: Path) -> str:
    st = folded_state(repo)
    for item in st.items.values():
        if item.kind == "phase":
            return item.id
    return "P1"


def state_part_digest(repo: Path) -> str:
    """One digest of the user's records, folded from a project's log the way the log reads it."""
    return D.of_obj(users_part(state_digests(repo)))
