"""`ddflow upgrade --status | --history | --resume`: watch an upgrade and finish an interrupted one.

Decision D-self-upgrade (6), task B-upgrade.11-orchestrate-monitor. Read-only over the folded log
(`State.upgrades`, `State.upgrade_runs`, `State.ddflow_versions`, `State.skew_overrides`), the
cached release check and the backups, plus the plan the running ddflow computes. Every step of
an apply is an `upgrade.applied` event (`services.upgrade_apply`), so what this module says is
what replay says.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..core.events import is_older, version_key
from ..core.model import State
from ..infra.log import running_version
from . import backups as BK
from . import upgrade_apply as UA
from . import upgrade_check as UC
from . import upgrade_plan as UP

#: The states a step shows.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
UNAVAILABLE = "unavailable"
NEEDS_OPERATOR = "needs operator confirmation"
WAIVED = "waived"

#: A run whose last record is younger than this is taken to be running; older, interrupted.
LIVE_S = 180.0

_STEP_STATE = {
    UA.APPLIED: DONE,
    UA.ACKNOWLEDGED: DONE,
    UA.FAILED: FAILED,
    UA.UNAVAILABLE: UNAVAILABLE,
    UA.REFUSED: NEEDS_OPERATOR,
    UA.SKIPPED: WAIVED,
}


def open_run(st: State) -> tuple[str, dict[str, Any]]:
    """`(id, run)` of the newest upgrade run that never closed, `("", {})` when there is none."""
    for rid, run in reversed(list(st.upgrade_runs.items())):
        if not run["closed"] and run["items"]:
            return rid, run
    return "", {}


def _last_touch(run: dict[str, Any]) -> str:
    return max([run["started"], *[s["at"] for s in run["steps"]]])


def run_state(run: dict[str, Any], now: float) -> str:
    """``running`` (touched within `LIVE_S`), ``interrupted``, ``done`` or ``failed``."""
    if run["closed"]:
        return run["closed"]
    return RUNNING if clock.age_s(_last_touch(run), now) < LIVE_S else "interrupted"


def _steps(
    plan: dict[str, Any], rid: str, run: dict[str, Any], live: bool, config_changes: str
) -> list[dict[str, Any]]:
    """Each step of the plan (and of the open run) with its state."""
    by_id = {s["id"]: s for s in run.get("steps", [])} if rid else {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # the apply works through the run's items in order: the first without a record is in flight
    current = next((i for i in run.get("items", []) if i not in by_id), "") if live else ""
    for cat in UP.CATEGORIES:
        for it in plan["categories"].get(cat) or []:
            seen.add(it["id"])
            rec = by_id.get(it["id"])
            if rec:
                state, detail = _STEP_STATE.get(rec["status"], PENDING), rec["detail"]
            elif it["action"] == UP.NOTE and cat not in UA._VERSIONED:
                state, detail = WAIVED, "a note: an apply lists it and changes nothing"
            elif UA._needs_confirmation(it, config_changes):
                state, detail = NEEDS_OPERATOR, ""
            elif it["id"] == current:
                state, detail = RUNNING, ""
            else:
                state, detail = PENDING, ""
            out.append(
                {
                    "id": it["id"],
                    "category": cat,
                    "state": state,
                    "summary": it["summary"],
                    "detail": detail,
                }
            )
    for sid, rec in by_id.items():  # settled by the run and so gone from the plan
        if sid not in seen:
            out.append(
                {
                    "id": sid,
                    "category": "",
                    "state": _STEP_STATE.get(rec["status"], PENDING),
                    "summary": rec["detail"],
                    "detail": rec["detail"],
                }
            )
    return out


def behind(st: State, running: str) -> list[dict[str, str]]:
    """Agents whose newest write was under a ddflow OLDER than ``running``: they, or the MCP
    server they started, must restart (or be upgraded) before they write with the new one."""
    last: dict[str, tuple[str, str]] = {}
    for version, rec in st.ddflow_versions.items():
        for agent in rec.get("agents") or []:
            cur = last.get(agent)
            if cur is None or version_key(version) > version_key(cur[0]):
                last[agent] = (version, str(rec.get("at", "")))
    out = [
        {"agent": a, "version": v, "at": at}
        for a, (v, at) in sorted(last.items())
        if is_older(v, running)
    ]
    for o in st.skew_overrides:
        # an agent with a stamp is judged by its newest one above; an override only speaks for
        # an agent that never stamped
        if (
            o["agent"] not in last
            and is_older(o["running"], running)
            and not any(x["agent"] == o["agent"] for x in out)
        ):
            out.append({"agent": o["agent"], "version": o["running"], "at": o["at"]})
    return out


def history(st: State) -> list[dict[str, Any]]:
    """The upgrades the log records, oldest first."""
    return [dict(u) for u in st.upgrades]


def status(
    repo: Path,
    cfg: Config,
    st: State,
    *,
    running: str = "",
    plan: dict[str, Any] | None = None,
    log: Any = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Where the upgrade stands, as plain data plus ``text``: the running version, the newest
    release and when it was asked, the project's stamp, the plan with each step's state, the
    backups, the agents still on an older version and the run that was interrupted, if any."""
    running = running or running_version()
    now = clock.now_utc().timestamp() if now is None else now
    if plan is None:
        plan = UP.build(repo, log, cfg, st, running=running)
    rid, run = open_run(st)
    state = run_state(run, now) if rid else ""
    cache = UC.read_cache(repo)
    newest = str(cache.get("newest", ""))
    steps = _steps(plan, rid, run, state == RUNNING, cfg.upgrade.config_changes)
    data: dict[str, Any] = {
        "running": running,
        "newest": newest,
        "newer": bool(newest) and is_older(running, newest),
        "checked_at": cache.get("checked_at"),
        "project_version": plan["project_version"],
        "up_to_date": plan["up_to_date"],
        "run": {"id": rid, "state": state, "categories": run.get("categories", [])} if rid else {},
        "steps": steps,
        "backups": [*BK.local_backups(repo), *BK.snapshots(repo)],
        "behind": behind(st, running),
        "last_upgrade": dict(st.upgrades[-1]) if st.upgrades else {},
    }
    data["text"] = render(data)
    return data


def render(d: dict[str, Any]) -> str:
    """The status as text."""
    checked = d["checked_at"]
    when = clock.iso_at(checked)[:16] + " UTC" if isinstance(checked, (int, float)) else "never"
    lines = [
        f"running ddflow {d['running']}",
        f"newest release {d['newest'] or 'unknown'} (last checked {when})"
        + (": a newer release exists, `ddflow upgrade --self`" if d["newer"] else ""),
        f"project stamp {d['project_version'] or '(none: predates version stamps)'}",
    ]
    if d["run"]:
        lines.append(
            f"upgrade run {d['run']['id']}: {d['run']['state']}"
            + (
                " -- finish it with `ddflow upgrade --resume`"
                if d["run"]["state"] != RUNNING
                else ""
            )
        )
    if d["steps"]:
        lines.append(f"plan ({len(d['steps'])} step(s)):")
        for s in d["steps"]:
            lines.append(f"  [{s['state']}] {s['id']}: {s['summary']}")
    else:
        lines.append("plan: up to date, nothing to do")
    la = d["last_upgrade"]
    if la:
        lines.append(f"last upgrade: {la['summary'] or la['to']} ({la['at'][:16]})")
    lines.append(
        "backups: "
        + (", ".join(d["backups"]) if d["backups"] else "none")
        + "  (restore with `ddflow upgrade --restore NAME`)"
    )
    if d["behind"]:
        lines.append("still on an older ddflow (restart the agent or its MCP server):")
        for b in d["behind"]:
            lines.append(f"  {b['agent']}: {b['version']} (on that version since {b['at'][:16]})")
    return "\n".join(lines)


def render_history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no upgrade has been applied to this project"
    lines = [f"{len(rows)} upgrade(s):"]
    for u in rows:
        what = ", ".join(u["categories"]) or "nothing"
        lines.append(
            f"  {u['at'][:16]}  {u['from'] or 'unstamped'} -> {u['to']}  [{what}]  "
            f"{len(u['items'])} item(s) by {u['agent']}"
            + (f"  backup {u['backup']}" if u["backup"] else "")
        )
    return "\n".join(lines)
