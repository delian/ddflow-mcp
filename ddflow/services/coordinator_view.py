"""The coordinator's one screen: suites, lease holders, approvals, ready work, a blocked chain.

A pure function of data ddflow already keeps (the lane ledger, the folded state, the plan, the
suite lock's notes handed in as ``suites``): nothing is written, spawned or killed. Every
section is cut at ``[lanes].coordinator_max_rows`` and says how many rows it left out.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..core.clock import epoch, fmt_age
from .approvals import view as AP
from .lanes import states as S
from .lanes.safe import safe


def _cut(rows: list, cap: int, partial: dict[str, int], name: str) -> list:
    if cap > 0 and len(rows) > cap:
        partial[name] = len(rows) - cap
        return rows[:cap]
    return rows


def _chain(item: str, st, plan, now: float, grace_s: int) -> list[dict[str, Any]]:
    blocked = {b.item: b for b in plan.blocked}
    ready = {i.id for i in plan.ready}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: str, depth: int) -> None:
        if node in seen:  # each item once: the walk is finite and a cycle ends
            return
        seen.add(node)
        b = blocked.get(node)
        it = st.items.get(node)
        if b is not None:
            out.append(
                {"item": node, "depth": depth, "reason": b.reason, "detail": safe(b.detail or "")}
            )
            for dep in b.waiting_on:
                walk(dep, depth + 1)
        elif it is None:
            out.append(
                {"item": node, "depth": depth, "reason": "unknown", "detail": "no such item"}
            )
        elif it.lease and it.lease.live(now, grace_s):
            out.append(
                {
                    "item": node,
                    "depth": depth,
                    "reason": "held",
                    "detail": f"held by {safe(it.lease.holder)} ({it.state})",
                }
            )
        else:
            reason = "ready" if node in ready else it.state
            out.append({"item": node, "depth": depth, "reason": reason, "detail": ""})

    walk(item, 0)
    return out


def build(
    ledger: Sequence[Any],
    st,
    cfg,
    now: float,
    plan,
    *,
    suites: Sequence[dict[str, Any]] = (),
    item: str = "",
) -> dict[str, Any]:
    """The view as a dict: suites, holders, free, approvals, ready, chain, owners, partial."""
    cap = int(cfg.lanes.coordinator_max_rows)
    idle_s = int(cfg.lanes.idle_s)
    partial: dict[str, int] = {}
    held = [ln for ln in ledger if ln.state in S.ACTIVE]
    holders = []
    for ln in held:
        quiet = max(0, int(now - max(ln.heartbeat, ln.last_event)))
        holders.append(
            {
                "lane": ln.name,
                "item": ln.item,
                "state": ln.state,
                "quiet_s": quiet,
                "idle": idle_s > 0 and quiet >= idle_s,
            }
        )
    owners = dict(
        _cut(list({h["lane"]: h["item"] for h in holders}.items()), cap, partial, "owners")
    )
    asks = [
        {
            "id": r["id"],
            "age_s": max(0, int(now - epoch(r["requested_at"], default=0.0))),
            "by": r["requested_by"],
            "kind": r["type"],
            "command": safe(r["canon"] or r["text"])[:200],
            "form": AP.answer_form(r),
        }
        for r in AP.waiting_for_a_person(st, now)
    ]
    per_phase: dict[str, int] = {}
    for it in plan.ready:
        per_phase[it.parent or "-"] = per_phase.get(it.parent or "-", 0) + 1
    data = {
        "suites": _cut([dict(s) for s in suites], cap, partial, "suites"),
        "holders": _cut(holders, cap, partial, "holders"),
        "free": _cut([ln.name for ln in ledger if ln.state in S.FREE], cap, partial, "free"),
        "approvals": _cut(asks, cap, partial, "approvals"),
        "ready": per_phase,
        "chain": _cut(_chain(item, st, plan, now, cfg.lease.grace_s), cap, partial, "chain")
        if item
        else [],
        "item": item,
        "owners": owners,
        "partial": partial,
    }
    if cap > 0 and len(per_phase) > cap:
        partial["ready"] = len(per_phase) - cap
        data["ready"] = dict(list(per_phase.items())[:cap])
    return data


def render(d: dict[str, Any]) -> str:
    """Plain text: one block per section, a partial-list notice under each cut one."""
    p = d["partial"]

    def more(name: str) -> list[str]:
        return [f"  ({p[name]} more {name} not shown)"] if name in p else []

    out = ["SUITES"]
    out += [
        f"  pid {s.get('pid', '?')} {safe(str(s.get('holder', '?')))} running {fmt_age(s.get('age_s') or 0, 'm')}"
        for s in d["suites"]
    ] or ["  none running"]
    out += more("suites")
    out.append("HOLDERS")
    out += [
        f"  {safe(h['lane'])}  {safe(h['item'])}  {h['state']}  quiet {fmt_age(h['quiet_s'], 'm')}"
        + ("  idle" if h["idle"] else "")
        for h in d["holders"]
    ] or ["  none"]
    out += more("holders")
    if d["free"]:
        out.append("  free: " + ", ".join(safe(n) for n in d["free"]))
    out += more("free")
    out.append("APPROVALS")
    out += [
        f"  {a['id']}  {fmt_age(a['age_s'], 'm')}  {safe(a['by'])}: {a['command']}  -> {a['form']}"
        for a in d["approvals"]
    ] or ["  none pending"]
    out += more("approvals")
    out.append("READY")
    out += [f"  {ph}: {n}" for ph, n in d["ready"].items()] or ["  none"]
    out += more("ready")
    if d["item"]:
        out.append(f"CHAIN {safe(d['item'])}")
        out += [
            f"  {'  ' * c['depth']}{safe(c['item'])}: {c['reason']}"
            + (f" — {c['detail']}" if c["detail"] else "")
            for c in d["chain"]
        ]
        out += more("chain")
    out.append("OWNERS")
    out += [f"  {safe(a)} -> {safe(i)}" for a, i in d["owners"].items()] or ["  none"]
    out += more("owners")
    return "\n".join(out)


def render_holders(d: dict[str, Any]) -> str:
    """`ddflow lease`: the holders block of the screen, alone."""
    return (
        render({**d, "suites": [], "approvals": [], "ready": {}, "item": "", "chain": []})
        .split("HOLDERS\n", 1)[1]
        .split("\nAPPROVALS", 1)[0]
    )
