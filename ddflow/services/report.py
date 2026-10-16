"""The completion report of one item, gathered from the log: `ddflow report ITEM`.

What an agent's last message used to retell in 400-700 words -- the shas, the regression tests
and whether `complete` verified them, each gate's outcome and who reviewed, the gates recorded by
hand, what ran on an earlier tree, the overrides, what was filed, what is still open and what was
not done -- is all already in the log. This module reads it back as one fixed dict; `report_render`
turns that into the text and the template (`agent_report.md`) lays it out. Nothing here writes.
"""

from __future__ import annotations

from typing import Any

from ..core.records import State
from ..infra import worktree as W
from . import gates as G
from . import ledger as LG
from . import report_gates as RG

#: Commits listed for the landing; the rest is a count.
MAX_COMMITS = 20


def _bug_tests(events, st: State, item_id: str) -> list[dict[str, Any]]:
    """The bugs this item fixed, with their regression tests and what `complete`/`bug fixed`
    made of them: ``verified`` (failed on the unfixed code), ``overridden`` (with the reason),
    ``could-not-run`` or ``not-applicable``."""
    it = st.items[item_id]
    mine = {b.id for b in st.bugs.values() if b.fix_task == item_id} | set(it.fixes)
    fixed = {}
    for ev in events:  # the last close of each bug wins
        if ev.kind == "bug.fixed" and ev.subject in mine:
            fixed[ev.subject] = ev.data
    rows = []
    for bid in sorted(mine):
        data = fixed.get(bid, {})
        bug = st.bugs.get(bid)
        verify = (
            data.get("regression_verify") if isinstance(data.get("regression_verify"), dict) else {}
        )
        rows.append(
            {
                "bug": bid,
                "summary": ((bug.title or bug.summary)[:120] if bug else ""),
                "tests": list(
                    data.get("regression_tests") or (bug.regression_tests if bug else [])
                ),
                "verified": data.get("regression_verified", "") or ("" if data else "not closed"),
                "reason": str(verify.get("reason") or "")[:240],
                "open": bool(bug and bug.open),
            }
        )
    return rows


def _window(events, item_id: str, who: str) -> tuple[str, str]:
    """(start, end) of the work: the first claim by ``who`` when they held the item, else the
    item's first claim, and its completion ("" for the open end). Only the window is widened by
    the fallback: ``_filed`` still keeps just the records ``who`` wrote.
    """
    claims = [ev for ev in events if ev.kind == "lease.acquired" and ev.subject == item_id]
    # The claim of ``who``; the item's first claim when ``who`` never held it (an agent that
    # completed work another claimed).
    mine = [ev for ev in claims if not who or ev.data.get("holder") == who]
    start = (mine or claims)[0].ts if claims else ""
    end = max(
        (ev.ts for ev in events if ev.kind == "item.completed" and ev.subject == item_id),
        default="",
    )
    return start, end


def _filed(events, st: State, item_id: str, who: str) -> dict[str, list[dict[str, Any]]]:
    """Bugs and tasks recorded while the item was worked: against it, or by ``who`` between its
    claim and its completion. Each with whether it is still open."""
    start, end = _window(events, item_id, who)

    def inside(ev) -> bool:
        return (
            bool(start)
            and ev.ts >= start
            and (not end or ev.ts <= end)
            and (not who or ev.agent == who)
        )

    bugs, tasks = {}, {}
    for ev in events:
        if ev.kind == "bug.found" and ev.subject in st.bugs:
            if st.bugs[ev.subject].item == item_id or inside(ev):
                bugs[ev.subject] = st.bugs[ev.subject]
        elif (
            ev.kind == "task.added"
            and ev.subject != item_id
            and ev.subject in st.items
            and inside(ev)
        ):
            tasks[ev.subject] = st.items[ev.subject]
    return {
        "bugs": [
            {
                "id": b.id,
                "title": (b.title or b.summary)[:100],
                "open": b.open,
                "fix_task": b.fix_task,
            }
            for b in bugs.values()
        ],
        "tasks": [
            {"id": t.id, "title": t.title[:100], "open": t.state not in ("done", "abandoned")}
            for t in tasks.values()
        ],
    }


def _landing(repo, it, led: dict[str, Any], gates: list[dict[str, Any]]) -> dict[str, Any]:
    merge = next((g for g in gates if g["gate"] == "merge"), {})
    head = merge.get("branch_head", "")
    commits: list[str] = []
    if it.landed_before and head:
        r = W.git(repo, "log", "--format=%h", f"{it.landed_before}..{head}")
        commits = r.out.split() if r.ok else []
    return {
        "merge_sha": it.merged_sha or led.get("sha", ""),
        "branch": it.branch,
        "branch_head": head,
        "base": it.base,
        "commits": commits[:MAX_COMMITS],
        "commits_total": len(commits),
        "completed_at": led.get("completed_at", "") if led else it.completed_at,
        "completed_by": led.get("completed_by", "") if led else "",
    }


def collect(repo, events, st: State, cfg, item_id: str, agent: str = "") -> dict[str, Any] | None:
    """The report of ``item_id``, or None when there is no such item. ``agent`` narrows what was
    filed to that agent's records (default: the agent that completed the item)."""
    it = st.items.get(item_id)
    if it is None or it.removed:
        return None
    led = LG.build(events, item_id) or {}
    who = agent or led.get("completed_by", "") or (it.lease.holder if it.lease else "")
    rows = RG.rows(st, cfg, it, events)
    landed = it.merged_sha or led.get("sha", "")
    stale = RG.earlier_tree(st, cfg, item_id, repo, landed)
    for r in rows:
        r["earlier_tree"] = stale.get(r["gate"], "")
    filed = _filed(events, st, item_id, who)
    tests = _bug_tests(events, st, item_id)
    overrides = RG.overrides(it, led, tests, rows)
    pipeline = G.pipeline_for(it, cfg)
    not_done = RG.not_done(it, led, rows, pipeline, tests)
    follow = [f"{b['id']} (bug, open)" for b in filed["bugs"] if b["open"]]
    follow += [f"{t['id']} (task, open)" for t in filed["tasks"] if t["open"]]
    return {
        "item": {"id": it.id, "title": it.title, "kind": it.kind, "state": it.state, "agent": who},
        "landing": _landing(repo, it, led, rows),
        "regression": {"bugs": tests, "test_files": list(led.get("done", {}).get("tests", []))},
        "gates": rows,
        "overrides": overrides,
        "filed": filed,
        "follow_ups": follow,
        "not_done": not_done,
    }
