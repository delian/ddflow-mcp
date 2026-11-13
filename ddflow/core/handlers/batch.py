"""The fold of the `batch.*` events: a batch of items verified by one gate run (D-BG-3).

`State.batches` maps a batch id to a plain dict: its members (item, tip, paths), the trigger and
base it formed on, the gate runs (`gates`, oldest first), the culprits the search named, and its
state (`formed` -> `landed` | `dissolved`) with the reason it was dissolved for. Batch events are
never pruned (D-BG-Q10): a replay rebuilds the same dict. An event naming a batch that was never
formed is ignored, not an error: the fold stays total. Pure."""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _num(v: Any) -> float | int | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _members(raw: Any) -> list[dict[str, Any]]:
    out = []
    for m in raw if isinstance(raw, list) else []:
        if isinstance(m, dict) and isinstance(m.get("item"), str):
            paths = m.get("paths")
            out.append(
                {
                    "item": m["item"],
                    "tip": _str(m.get("tip")),
                    "paths": [p for p in paths if isinstance(p, str)]
                    if isinstance(paths, list)
                    else [],
                }
            )
    return out


def h_batch_formed(st: State, ev: Event) -> None:
    d = ev.data if isinstance(ev.data, dict) else {}
    if ev.subject in st.batches:
        return  # a batch id is formed once; a replayed or racing duplicate changes nothing
    st.batches[ev.subject] = {
        "id": ev.subject,
        "state": "formed",
        "members": _members(d.get("members")),
        "trigger": _str(d.get("trigger")),
        "base": _str(d.get("base")),
        "gates": [],
        "culprits": [],
        "reason": "",
        "sha": "",
        "at": ev.ts,
    }


def h_batch_gate_started(st: State, ev: Event) -> None:
    b = st.batches.get(ev.subject)
    d = ev.data if isinstance(ev.data, dict) else {}
    if b is None:
        return
    b["gates"].append(
        {
            "gate": _str(d.get("gate")),
            "run": _str(d.get("run")),
            "tree_sha": _str(d.get("tree_sha")),
            "command_digest": _str(d.get("command_digest")),
            "started": ev.ts,
            "outcome": "",
        }
    )


def h_batch_gate_finished(st: State, ev: Event) -> None:
    b = st.batches.get(ev.subject)
    d = ev.data if isinstance(ev.data, dict) else {}
    if b is None:
        return
    run = _str(d.get("run"))
    gate = _str(d.get("gate"))
    # The open start of the same run (or, with no run id, of the same gate) is closed; a
    # finish with no start is its own entry, so a run that was never announced still shows.
    if run and any(g["run"] == run and g.get("finished") for g in b["gates"]):
        return  # a run closes once; a second finish of the same run id changes nothing
    entry = next(
        (
            g
            for g in reversed(b["gates"])
            if not g.get("finished") and (g["run"] == run if run else g["gate"] == gate)
        ),
        None,
    )
    if entry is None:
        entry = {"gate": gate, "run": run, "tree_sha": "", "command_digest": "", "started": None}
        b["gates"].append(entry)
    entry.update(
        outcome=_str(d.get("outcome")),
        tree_sha=_str(d.get("tree_sha")) or entry.get("tree_sha", ""),
        elapsed_s=_num(d.get("elapsed_s")),
        queue_wait_s=_num(d.get("queue_wait_s")),
        reason=_str(d.get("reason")),
        members=[m for m in d.get("members", []) if isinstance(m, str)]
        if isinstance(d.get("members"), list)
        else [],
        finished=ev.ts,
    )


def h_batch_culprit(st: State, ev: Event) -> None:
    b = st.batches.get(ev.subject)
    d = ev.data if isinstance(ev.data, dict) else {}
    if b is not None:
        runs = d.get("runs")
        b["culprits"].append(
            {"member": _str(d.get("member")), "runs": runs if isinstance(runs, int) else 0}
        )


def h_batch_landed(st: State, ev: Event) -> None:
    b = st.batches.get(ev.subject)
    if b is not None and b["state"] == "formed":
        b["state"] = "landed"
        b["sha"] = _str((ev.data if isinstance(ev.data, dict) else {}).get("sha"))


def h_batch_dissolved(st: State, ev: Event) -> None:
    b = st.batches.get(ev.subject)
    if b is not None and b["state"] == "formed":
        b["state"] = "dissolved"
        b["reason"] = _str((ev.data if isinstance(ev.data, dict) else {}).get("reason"))


BATCH_HANDLERS = {
    "batch.formed": h_batch_formed,
    "batch.gate_started": h_batch_gate_started,
    "batch.gate_finished": h_batch_gate_finished,
    "batch.culprit": h_batch_culprit,
    "batch.landed": h_batch_landed,
    "batch.dissolved": h_batch_dissolved,
}
