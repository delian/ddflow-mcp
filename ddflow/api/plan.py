"""`ddflow plan check`: mechanical checks on a PROPOSED plan, before anything is filed.

The checks themselves are `services/plancheck.py` (pure, no I/O); this is the api around
them: read the plan, give the checks the repository's tracked files and the queue's
duplicate hunt, and report. `ddflow plan check -` reads the plan from stdin.

Findings are the whole point, so they are a FAILURE (exit 1) with the findings as data; a
plan that passes is exit 0 and carries its waves. A plan that cannot be read at all is a
REFUSAL (exit 3): "could not tell" is never a clean plan.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..infra import git as G
from ..services import plancheck as PC
from ._globwarn import missing_globs


def _items(data: Any) -> dict[str, Any] | None:
    """The plan as {id: item} from whatever the document holds, or None if it holds none."""
    if isinstance(data, list):
        return {str(t["id"]): t for t in data if isinstance(t, dict) and t.get("id")}
    if not isinstance(data, dict):
        return None
    for key in ("items", "tasks", "phases"):
        if isinstance(data.get(key), dict):
            return {str(k): v for k, v in data[key].items()}
        if isinstance(data.get(key), list):
            return {str(t["id"]): t for t in data[key] if isinstance(t, dict) and t.get("id")}
    # A plan may be the mapping itself. An item is one that says what it needs or touches;
    # anything else in the document (a title, a version) is not an item and is left out.
    plan = {
        str(k): v
        for k, v in data.items()
        if isinstance(v, dict) and ("needs" in v or "globs" in v or "acceptance" in v)
    }
    return plan or None


def _parse(text: str, source: str) -> dict[str, Any] | str:
    """The plan, or a sentence saying why the document is not one."""
    try:
        data: Any = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        try:
            data = json.loads(text)
        except ValueError as exc:
            return f"{source} is neither TOML nor JSON ({exc})"
    plan = _items(data)
    if plan is None:
        return (
            f"{source} holds no items: expected {{id = {{ needs = [...], globs = [...] }}}} "
            f"(or a list under `items`/`tasks`/`phases`)"
        )
    return plan


def _tracked(repo: Path) -> list[str]:
    """The repository's tracked files, for the glob that matches more than it reads as.

    Empty when git cannot list them: the check then simply does not make that comparison,
    and says so in nothing -- a plan is not refused because a listing failed.
    """
    return G.files(repo, "tracked") or []


def plan_check(repo: Path, source: str) -> O.Outcome:
    """Check the plan at ``source`` ("-" for stdin) and report what to fix before filing."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    except OSError as exc:
        return O.failed("plan.check", f"cannot read the plan: {exc}")
    plan = _parse(text, source)
    if isinstance(plan, str):
        return O.refused("plan.check", plan)
    tracked = _tracked(repo)
    report = PC.check(plan, tracked=tracked)
    cfg = Config.load(repo)
    # Warnings, not findings: a path the plan does not find may be a file it creates.
    warnings = [
        {"id": iid, "detail": w}
        for iid in sorted(plan)
        for w in missing_globs(repo, cfg, [str(g) for g in plan[iid].get("globs", [])], tracked)
    ]
    data: dict[str, Any] = {
        "source": source,
        "clean": report.clean,
        "findings": [f.as_dict() for f in report.findings],
        "waves": dict(sorted(report.waves.items(), key=lambda kv: (kv[1], kv[0]))),
        "items": len(plan),
        "warnings": warnings,
    }
    if report.findings:
        kinds = ", ".join(sorted({f.kind for f in report.findings}))
        return O.failed(
            "plan.check", f"{len(report.findings)} finding(s) in {source}: {kinds}", **data
        )
    return O.ok("plan.check", **data)
