"""Collectors that read items, decisions, gates, CI results and health facts (OD-queue).

Each is `collect_<kind>(st, cfg, now, facts) -> list[Entry]`, pure. A fact State does not carry
(health needs a worktree's newest change) arrives in `facts`; without it the lease collector
reports nothing and the queue lists the gap (`queue.Queue.gaps`), never a clean bill.
"""

from __future__ import annotations

import re

from ...core import clock
from ...core import progress as PR
from ...core.model import ABANDONED, DONE, OPEN, State
from ..gates import outcomes as GO
from . import entry as N

#: Rounds of the stage that checks main after a merge or on a schedule.
MAIN_STAGES = ("merge", "schedule")
_KEY_REASON = re.compile(
    r"\b(api[ _-]?key|credentials?|token|secret|KIMI_API_KEY|unauthori[sz]ed|401|403)\b",
    re.I,
)
_DEFAULT_LINE = re.compile(r"^.*\bdefault\b.*$", re.I | re.M)


def _live(st: State):
    return [it for it in sorted(st.items.values(), key=lambda i: i.id) if not it.removed]


def _is_open(it) -> bool:
    return it.state not in (DONE, ABANDONED)


def collect_decide(st, cfg, now, facts):
    out = []
    for it in _live(st):
        if it.kind != "doc" or it.state != OPEN or not it.title.upper().startswith("DECIDE"):
            continue
        if any(st.items.get(n) is not None and st.items[n].state != DONE for n in it.needs):
            continue  # not askable yet: what it waits for is not done
        m = _DEFAULT_LINE.search(it.body or "")
        default = N.clip(m.group(0), cfg) if m else "the defaults listed in the task"
        out.append(
            N.make(
                st,
                cfg,
                now,
                "decide",
                it.id,
                what=f"{it.id}: {N.clip(it.title, cfg)}",
                why="the lead cannot answer for the operator; the task lists the questions",
                since=max(
                    [it.created_at, *(st.items[n].completed_at for n in it.needs if n in st.items)]
                ),
                default=default,
                command=f"ddflow decision add --id D-{it.id}-Q1 --title <title> --decision <text>",
                blocks=[it.id],
                detail={"task": it.id},
            )
        )
    return out


def collect_phase_close(st, cfg, now, facts):
    out = []
    for p in _live(st):
        if p.kind != "phase" or not _is_open(p) or not PR.phase_tally(st, p.id).complete:
            continue
        done_at = max((t.completed_at for t in st.tasks(p.id) if t.completed_at), default="")
        out.append(
            N.make(
                st,
                cfg,
                now,
                "phase_close",
                p.id,
                what=f"phase {p.id} has every task done",
                why="a finished phase stays open until its pipeline runs and it is completed",
                since=done_at or p.created_at,
                default="close it",
                command=f"ddflow complete {p.id}",
                blocks=[p.id],
            )
        )
    return out


def collect_decision(st, cfg, now, facts):
    return [
        N.make(
            st,
            cfg,
            now,
            "decision",
            d.id,
            what=f"decision {d.id} is proposed: {N.clip(d.title, cfg)}",
            why="a proposed decision binds no one until a person accepts or supersedes it",
            since=d.at,
            default="accept it as written, unless you want to change it",
            command=f"ddflow decision show {d.id}",
            detail={"globs": list(d.globs)},
        )
        for d in sorted(st.decisions.values(), key=lambda d: d.id)
        if d.status == "proposed"
    ]


def collect_gate_spotcheck(st, cfg, now, facts):
    floor = cfg.digest.spotcheck_min
    if not floor:
        return []
    rows = [r for r in GO.refuted_passes(st) if _is_open(st.items[r["item"]])]
    if len(rows) < floor:
        return []
    items = sorted({r["item"] for r in rows})
    since = min((r["at"] for r in rows if r["at"]), default="")
    lines = [GO.refuted_line(r) for r in rows]
    return [
        N.make(
            st,
            cfg,
            now,
            "gate_spotcheck",
            "refuted",
            what=f"{len(rows)} gate(s) passed on refutation await your spot-check",
            why="a reviewer's finding was refuted by its author, not fixed; the pass stands only if the refutation holds",
            since=since,
            default="skim the refutations; reopen one you doubt",
            blocks=[],
            detail={"items": items, "gates": lines},
        )
    ]


def collect_credential(st, cfg, now, facts):
    out = []
    for it in _live(st):
        if not _is_open(it):
            continue
        for gate, rec in sorted(it.gates.items()):
            if rec.outcome == "unavailable" and _KEY_REASON.search(rec.reason or ""):
                out.append(
                    N.make(
                        st,
                        cfg,
                        now,
                        "credential",
                        f"{it.id}.{gate}",
                        what=f"{it.id}.{gate} could not run: a credential is missing or refused",
                        why="the reviewer reported a missing or refused credential; the gate record has the detail (it is not copied here: it may quote a secret)",
                        since=rec.at,
                        default="provide the credential, then re-run the gate (never paste it in chat)",
                        blocks=[it.id],
                        agents=[rec.by] if rec.by else [],
                    )
                )
    return out


def collect_red_main(st, cfg, now, facts):
    streak = []
    for r in reversed(st.ci_results):
        if r.get("stage") not in MAIN_STAGES:
            continue
        if r.get("ok"):
            break
        streak.append(r)
    if not streak:
        return []
    first = streak[-1]
    failed = N.clip(", ".join(str(c) for c in first.get("checks") or []), cfg)
    return [
        N.make(
            st,
            cfg,
            now,
            "red_main",
            (first.get("sha") or "main")[:12],
            what=f"main is red since {clock.fmt_minute(first['at'])}",
            why=f"the {first.get('stage')} check failed" + (f": {failed}" if failed else ""),
            since=first["at"],
            default="have the fix task go first; nothing else should merge meanwhile",
            detail={"failures_in_a_row": len(streak), "sha": first.get("sha", "")},
        )
    ]


def collect_lease(st, cfg, now, facts):
    if facts.health is None:
        return []
    out = []
    for f in facts.health:
        if f.kind not in N.HEALTH_KINDS:
            continue
        out.append(
            N.make(
                st,
                cfg,
                now,
                "lease",
                f"{f.kind}:{f.item or f.holder}",
                what=f"{f.kind.upper()} {f.item or '-'} held by {f.holder or '-'}",
                why=N.clip(f.detail, cfg),
                since=clock.iso_at(now - f.age_s),
                default=f.fix,
                command=f.fix if f.fix.startswith("ddflow ") else "ddflow health",
                blocks=[f.item] if f.item in st.items else [],
                agents=[f.holder] if f.holder else [],
            )
        )
    return out


def collect_agent_failed(st, cfg, now, facts):
    """Nothing in State records a run's failure text yet (queue.UNCOLLECTED names the gap)."""
    return []
