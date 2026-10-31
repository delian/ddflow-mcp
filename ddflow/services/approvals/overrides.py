"""The overrides on the record (RA-audit-wire-2, D-RA-5): every gate or refusal set aside, and who
approved it at which level. One reader over the event log and the ask fold, shared by
`ddflow gate list --overrides` and the `doctor` notes (r-reuse-core). It carries ids, names,
levels, counts and times -- never an approval's answer text, proof or any secret value."""

from __future__ import annotations

from typing import Any

from ...core.handlers.asks import asks_of
from ..ask import engine as E
from . import harness as H

FORCED = "forced completion"
SKIP = "gate skip"
APPROVED_SKIP = "approved gate skip"
CLAIM_FORCE = "claim force"
RELAYED = "relayed consent"
TYPED = "typed-chat T3 use"
KINDS = (FORCED, SKIP, APPROVED_SKIP, CLAIM_FORCE, TYPED, RELAYED)


def _who(stamp: dict[str, Any]) -> dict[str, Any]:
    return {
        "approver": str(stamp.get("approved_by", "")),
        "channel": str(stamp.get("via", "")),
        "level": stamp.get("level") or 0,
        "grade": str(stamp.get("grade", "")),
        "ask": str(stamp.get("ask", "")),
    }


def _row(kind: str, item: str, gate: str, by: str, at: str, stamp: dict[str, Any], detail: str):
    return {"kind": kind, "item": item, "gate": gate, "by": by, "at": at, "detail": detail,
            **_who(stamp)}  # fmt: skip


def rows(events, state, cfg) -> list[dict[str, Any]]:
    """Every override, oldest first."""
    out: list[dict[str, Any]] = []
    for ev in events:
        d = ev.data or {}
        if ev.kind == "item.completed" and d.get("forced"):
            named = "; ".join(str(x) for x in d.get("overridden") or []) or "(nothing named)"
            out.append(_row(FORCED, ev.subject, "", ev.agent, ev.ts, d, f"overrode: {named}"))
        elif ev.kind == "gate.skipped":
            stamp = (d.get("evidence") or {}).get("approval") or {}
            kind = APPROVED_SKIP if stamp else SKIP
            out.append(_row(kind, ev.subject, str(d.get("gate", "")), str(d.get("by") or ev.agent),
                            ev.ts, stamp, str(d.get("reason", ""))))  # fmt: skip
        elif ev.kind == "skew.overridden":
            out.append(_row(RELAYED, ev.subject, "", ev.agent, ev.ts, {}, "skew.overridden"))
    for ask in asks_of(state).values():
        for c in ask["consumed"]:
            stamp = {"approved_by": ask["answered_by"], "via": ask["via"], "ask": ask["id"],
                     "level": E.verified_level(ask, cfg), "grade": c["grade"]}  # fmt: skip
            if c["verb"] == "claim.force":
                out.append(_row(CLAIM_FORCE, ask["subject"], "", c["by"], c["at"], stamp,
                                c["verb"]))  # fmt: skip
            if c["grade"] == H.GRADE_T3_CHAT:
                out.append(_row(TYPED, ask["subject"], "", c["by"], c["at"], stamp,
                                c["verb"]))  # fmt: skip
    return sorted(out, key=lambda r: r["at"])


def line(r: dict[str, Any]) -> str:
    who = f" approved by {r['approver']} via {r['channel']} L{r['level']}" if r["approver"] else ""
    where = f" {r['item']}" + (f".{r['gate']}" if r["gate"] else "")
    ask = f", approval {r['ask']}" if r["ask"] else ""
    return f"{r['at'][:19]}  {r['kind']}{where} by {r['by']}{who}{ask}: {r['detail']}".rstrip(": ")


def notes(events, state, cfg) -> list[str]:
    """One doctor note per kind present: how many, and the latest (recency, by whom)."""
    found = rows(events, state, cfg)
    out = []
    for kind in KINDS:
        mine = [r for r in found if r["kind"] == kind]
        if mine:
            last = mine[-1]
            out.append(
                f"approvals audit: {len(mine)} {kind}(s), latest {last['at'][:19]} by {last['by']}"
                f"{' on ' + last['item'] if last['item'] else ''}"
            )
    return out
