"""How `queue` reads on the command line. The operations are `api.mergequeue_add` and
`api.mergequeue_run`; the executor (`surfaces/cliexec.py`) prints what these return. It
imports nothing that loads the api (tests/test_declared_commands.py)."""

from __future__ import annotations

SECONDS_UNTIL_MINUTES = 90
SECONDS_UNTIL_HOURS = 5400


def _span(s: float) -> str:
    """A duration as a person reads an ETA: seconds, minutes, then hours."""
    s = int(s)
    if s < SECONDS_UNTIL_MINUTES:
        return f"{s}s"
    if s < SECONDS_UNTIL_HOURS:
        return f"{s // 60}m"
    return f"{s / 3600:.1f}h"


def status_text(d: dict) -> str:
    rows = d["entries"]
    who = d.get("integrator")
    head = (
        f"integrator: running ({who['agent'] or '?'}, pid {who['pid']})"
        if who
        else "integrator: idle -- `ddflow queue run` lands the line"
    )
    if not rows:
        return f"{head}\nthe merge queue is empty"
    lines = [head, f"{d['waiting']} waiting, {d['parked']} parked"]
    for r in rows:
        where = f"#{r['position']}" if r["position"] else "  "
        eta = f"  lands in ~{_span(r['eta_s'])}" if r["position"] else ""
        why = f"  -- {r['reason']}" if r["reason"] else ""
        lines.append(f"  {where:>3} {r['item']}  {r['state']}{eta}{why}")
    return "\n".join(lines)


def run_text(d: dict) -> str:
    lines = []
    for p in d["processed"]:
        sha = f" ({p['sha'][:8]})" if p["sha"] else ""
        why = f": {p['reason']}" if p["reason"] else ""
        lines.append(f"  {p['item']}  {p['state']}{sha}{why}")
    lines.append(
        f"{d['merged']} merged, {d['parked']} parked, {d['left']} still waiting"
        + (" -- `ddflow complete` each merged item" if d["merged"] else "")
    )
    return "\n".join(lines)


def text(out, a) -> str:
    """The human line(s) for any `queue` verb."""
    d = out.data
    verb = a.get("action") or "status"
    if verb == "add":
        return d.get("text") or out.reason
    if verb == "remove":
        return f"{d['id']} removed from the merge queue (was {d['was']})"
    if verb == "run":
        return run_text(d)
    return status_text(d)
