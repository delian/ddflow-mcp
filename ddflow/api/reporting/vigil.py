"""`ddflow vigil`: one compact health snapshot in one pass (bug B3a86c5cfb9).

The coordinator reconstructed its hourly snapshot via 5+ shell pipelines: open bug count
by severity, stale pins, active lanes + what each holds + lane age, the interrupted count,
pending approvals and reviewer reachability. `vigil` folds the log ONCE and reuses the
existing verbs' building blocks rather than reimplementing them:

* bug counts      -- the fold's ``st.bugs`` (`ddflow bug list` reads the same records)
* stale pins      -- ``services.pins.regenerate(check=True)`` (`ddflow pins regenerate --check`)
* active lanes    -- ``services.lanes.build`` (`ddflow lanes`)
* interrupted     -- the plan's ``interrupted`` (`ddflow status`)
* approvals       -- ``services.approvals.view.waiting_for_a_person`` (`ddflow approvals pending`)
* reviewers       -- ``services.review.model.load_reviewers`` + ``degraded_marks`` +
                     ``services.review.engine._url_ok`` (`ddflow reviewers test`'s reachability)

Read-only: nothing here writes an event, starts a process or opens a socket on its own.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import clock
from ...core import flowsignals as FSX
from ...core import outcome as O
from ...services import lanes as LN
from ...services.approvals import view as V
from ...services.review import engine as RVE
from ...services.review import model as RVM
from .._base import _load
from ..pin_files import pins_regenerate
from ..knowledge.bug_list import BUG_SEVERITIES
from ..knowledge.bug_list import BUG_SEVERITIES
from ..lifecycle.planning import plan_for

#: How far back a bug fixed still counts as "fixed in the window" for the snapshot.
VIGIL_FIXED_WINDOW_S = 7 * 24 * 3600


def _open_by_severity(bugs: dict[str, Any]) -> dict[str, int]:
    """Open bugs per declared severity, plus ``unspecified`` for one filed without one.

    Every key is always present, so both surfaces carry the same shape whether or not an
    open bug sits in that bucket (no null-padded lead)."""
    out: dict[str, int] = {sev: 0 for sev in BUG_SEVERITIES}
    out["unspecified"] = 0
    for b in bugs.values():
        if not b.open:
            continue
        sev = (b.severity or "").strip().lower()
        out[sev if sev in BUG_SEVERITIES else "unspecified"] += 1
    return out


def _fixed_in_window(bugs: dict[str, Any], now: float) -> int:
    """Bugs closed as fixed whose ``fixed_at`` is within ``VIGIL_FIXED_WINDOW_S``.

    ``not b.open`` is explicit rather than implied: a bug that was reopened reads open
    again, and a record must never count in both ``open_total`` and here."""
    return sum(
        1
        for b in bugs.values()
        if b.fixed_at and not b.open and clock.age_s(b.fixed_at, now) <= VIGIL_FIXED_WINDOW_S
    )


def _stale_pins(repo: Path, cfg) -> list[str] | None:
    """Stale pin-file names: ``pins regenerate --check``'s answer, narrowed to the names.

    ``None`` when the check could not run (a failed recipe, or the recipes could not be
    read): a check that did not run must not render as a clean "none". ``pins --check``
    remains the verb that names the cause."""
    try:
        out = pins_regenerate(repo, check=True, verb="regenerate")
    except Exception:
        return None
    stale = [p["name"] for p in (out.data.get("pins") or []) if p.get("stale")]
    if stale:
        return stale
    return [] if out.exit == O.OK else None


def _reviewer_rows(repo: Path, cfg, st) -> list[dict[str, str]] | None:
    """Each enabled reviewer's reachability: ``up``, ``down`` (nothing answering) or
    ``429`` (its own quota/exhaustion wall recorded on the last review). The fleet's
    slowness -- which ddflow measures as ONE ratio, not per reviewer -- is the separate
    ``reviewer_latency_ratio`` field, never a per-reviewer state (a healthy reviewer is not
    "slow" because its neighbour was). ``None``: the reviewer list could not be read."""
    try:
        reviewers = [r for r in RVM.load_reviewers(repo) if r.enabled]
    except Exception:
        return None
    marks = RVM.degraded_marks(st)
    rows: list[dict[str, str]] = []
    for r in reviewers:
        mark = marks.get(r.name)
        if mark:
            rows.append(
                {
                    "name": r.name,
                    "reachability": "429",
                    "reason": mark.get("reason") or "quota/exhaustion",
                }
            )
        else:
            rows.append(
                {
                    "name": r.name,
                    "reachability": "up" if _reviewer_up(r.base_url) else "down",
                    "reason": "",
                }
            )
    return rows


def _reviewer_up(url: str) -> bool:
    """Is something listening and answering at ``url``? ``review.engine._url_ok``'s question."""
    return RVE._url_ok(url)


def _render(data: dict[str, Any]) -> str:
    bugs = data["bugs"]
    by_sev = bugs["open_by_severity"]
    sev_line = ", ".join(
        f"{by_sev.get(sev, 0)} {sev}" for sev in (*BUG_SEVERITIES, "unspecified")
    )
    lines = [
        "# vigil",
        "",
        (
            "bugs: "
            f"{sev_line} open"
            f" ({bugs['fixed_in_window']} fixed in the last "
            f"{bugs['window_s'] // 86400}d)"
        ),
        "stale pins: " + (
            "none"
            if data["stale_pins"] == []
            else (
                "unavailable (could not check)"
                if data["stale_pins"] is None
                else ", ".join(data["stale_pins"])
            )
        ),
        f"lanes ({len(data['lanes'])} active):",
    ]
    lines += [
        f"  [{ln['state']}] {ln['name']}  item {ln['item'] or '-'}"
        f"  ({ln['lane_age_s']}s in state)"
        for ln in data["lanes"]
    ]
    lines.append(f"interrupted: {data['interrupted']}")
    lines.append(f"pending approvals: {data['approvals_pending']}")
    reviewers = data["reviewers"]
    if reviewers is None:
        lines.append("reviewers: unavailable")
    elif reviewers == []:
        lines.append("reviewers: none")
    else:
        lines.append("reviewers:")
        lines += [
            f"  [{r['reachability']}] {r['name']}"
            + (f"  ({r['reason']})" if r["reason"] else "")
            for r in reviewers
        ]
    if data["reviewer_latency_ratio"] is not None:
        lines.append(f"reviewer latency: {data['reviewer_latency_ratio']}x usual")
    lines.append("")
    return "\n".join(lines)


def vigil(repo: Path, *, agent: str = "") -> O.Outcome:
    """The compact health snapshot, one fold, one answer.

    Both surfaces share this one body; the prose is ``data["text"]``, built here so the
    CLI and the MCP tool can never disagree (the same B37 shape `status` is built to)."""
    log, cfg, st = _load(repo, agent)
    events = log.read_all()
    now = time.time()
    plan = plan_for(repo, log, cfg, st, purpose="view", agent=log.agent_id, now=now, events=events)
    by_severity = _open_by_severity(st.bugs)
    ledger = LN.build(events, st, cfg, now, plan)
    lanes = [
        {
            "name": d["name"],
            "item": d["item"],
            "state": d["state"],
            "lane_age_s": d["for_s"],
            "heartbeat_age_s": d["heartbeat_age_s"],
        }
        for d in (ln.as_dict(now) for ln in ledger)
    ]
    pending = V.waiting_for_a_person(st, now)
    reviewer_latency_ratio = FSX.reviewer_latency_ratio(events, now)
    data: dict[str, Any] = {
        "bugs": {
            "open_total": sum(by_severity.values()),
            "open_by_severity": by_severity,
            "fixed_in_window": _fixed_in_window(st.bugs, now),
            "window_s": VIGIL_FIXED_WINDOW_S,
        },
        "stale_pins": _stale_pins(repo, cfg),
        "lanes": lanes,
        "lanes_total": len(lanes),
        "interrupted": len(plan.interrupted),
        "approvals_pending": len(pending),
        "pending_approval_ids": [r["id"] for r in pending],
        "reviewers": _reviewer_rows(repo, cfg, st),
        "reviewer_latency_ratio": reviewer_latency_ratio,
    }
    out = O.ok("vigil", **data)
    out.data["text"] = _render(data)
    return out
