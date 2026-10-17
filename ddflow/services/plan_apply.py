"""Filing a checked plan: the pure parts (D-sdd-plan-modes, B-plan-apply-and-the-rest).

A plan applied from an agent or an imported spec lands PROPOSED: the existing BLOCKED state
with a reason naming the plan, so the scheduler, `next` and `claim` refuse it as they refuse
any parked item, and `plan confirm` is the one verb that releases it. Every task also
carries the label `plan:<id>`, which is how a plan is found again.

Pure: no I/O. The api writes what this decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core import digest as DG
from ..core.handlers.items import PROPOSED_PREFIX
from ..core.records import BLOCKED, State
from . import items as IT

LABEL = "plan:"


def plan_hash(plan: Mapping[str, Any]) -> str:
    """A digest of the plan's content: what was checked is what gets filed.

    Key order and the order of the items do not matter; a changed glob, dependency or body
    does. `apply` refuses a hash that is not this one -- a plan edited after it was read.
    """
    return DG.of_obj(plan, "sha256", size=None)


def default_plan_id(digest: str) -> str:
    return f"p{digest[:8]}"


def kind_of(item: Mapping[str, Any]) -> str:
    return "phase" if item.get("kind") == "phase" else "task"


def _list(item: Mapping[str, Any], key: str) -> list[str]:
    v = item.get(key, [])
    return [str(x) for x in ([v] if isinstance(v, str) else v)]


def body_of(item: Mapping[str, Any]) -> str:
    """The body, with an `acceptance` field carried into it as the `ACCEPTANCE:` line the
    gates and the checks read."""
    body = str(item.get("body", ""))
    acc = str(item.get("acceptance", "")).strip()
    if acc and "acceptance" not in body.lower():
        body = f"{body}\n\nACCEPTANCE: {acc}".strip()
    return body


def draft_of(iid: str, item: Mapping[str, Any], plan_id: str) -> IT.TaskDraft:
    """The task a plan item becomes, labelled `plan:<id>`."""
    parent = str(item.get("parent", "")) or None
    return IT.TaskDraft(
        iid,
        title=str(item.get("title", iid)),
        parent=parent,
        needs=_list(item, "needs"),
        globs=_list(item, "globs"),
        body=body_of(item),
        tags=[*_list(item, "tags"), f"{LABEL}{plan_id}"],
        priority=int(item["priority"]) if "priority" in item else None,
        extra={"proposed": plan_id},
    )


def filed_by(st: State, plan_id: str) -> list[str]:
    """The live items carrying the label of plan ``plan_id``."""
    label = f"{LABEL}{plan_id}"
    return sorted(i.id for i in st.live_items() if label in i.tags)


def live_ids(st: State) -> list[str]:
    """The ids in the queue that have not been removed."""
    return sorted(i.id for i in st.live_items())


def proposed_by(st: State, plan_id: str) -> list[str]:
    """The items of plan ``plan_id`` still waiting for the operator."""
    mine = f"{PROPOSED_PREFIX}{plan_id}:"
    return [
        i
        for i in filed_by(st, plan_id)
        if st.items[i].state == BLOCKED and st.items[i].blocked_reason.startswith(mine)
    ]


def order(plan: Mapping[str, Any]) -> list[str]:
    """Phases first, then tasks in an order that files a dependency before its dependant
    (ties by id), so a reader of the log sees a parent before its child."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(iid: str) -> None:
        if iid in seen or iid not in plan:
            return
        seen.add(iid)
        for dep in [*_list(plan[iid], "needs"), str(plan[iid].get("parent", ""))]:
            visit(dep)
        out.append(iid)

    for iid in sorted(plan, key=lambda i: (kind_of(plan[i]) != "phase", i)):
        visit(iid)
    return out
