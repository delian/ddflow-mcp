"""How the work loop's verbs read on the command line: the human text, the stderr notes and
the `--json` bodies that are not the MCP tools' (`declared/lifecycle.py` names them).

Nothing here decides anything: the operations are `api.lifecycle` and `api.gates`, and the
executor (`surfaces/cliexec.py`) prints what these return. Imports of heavier modules are
deferred to the function that needs them, so declaring the commands stays cheap.
"""

from __future__ import annotations

from collections.abc import Iterator

from ...core import clock
from ...core.outcome import FAIL, NOTHING, OK, REFUSED

#: How many offending files a refusal lists before summarising the rest.
MAX_LISTED_FILES = 10


def waiting(rows: list, head: str) -> str:
    """One line per registered waiter, under ``head``; "" when nobody waits."""
    if not rows:
        return ""
    lines = [head]
    for w in rows:
        what = w["item"] or (f"anything in {w['phase']}" if w["phase"] else "anything ready")
        lines.append(f"  {w['agent']} — for {what}, {clock.fmt_age(w['waiting_s'])} so far")
    return "\n" + "\n".join(lines)


# -- heartbeat, release ----------------------------------------------------------------


def heartbeat_notes(out, a, ctx) -> Iterator[str]:
    if out.data.get("globs_withheld"):
        yield f"  lease renewed WITHOUT {a['id']}'s newer globs/resources: {out.data['globs_withheld']}"


def heartbeat_text(out, a) -> str:
    from ...views.markdown import new_reports_line

    waiters = out.data.get("waiters", [])
    return (
        (f"renewed {a['id']}" if out.data["renewed"] else out.reason)
        + waiting(
            waiters,
            f"{len(waiters)} agent(s) are waiting on {a['id']}. Finishing, narrowing its "
            f"globs, or releasing it wakes them:",
        )
        + (
            "\n" + new_reports_line(a["id"], out.data.get("new_reports", 0))
            if out.data.get("new_reports")
            else ""
        )
    )


def release_text(out, a) -> str:
    return f"{'released' if out.data['released'] else 'no lease on'} {a['id']}" + waiting(
        out.data.get("woke", []), "woke:"
    )


# -- abandon, remove, block, unblock ---------------------------------------------------


def abandon_text(out, a) -> str:
    return f"{a['id']} abandoned: {a['reason']}"


def remove_text(out, a) -> str:
    return f"{a['id']} removed from the queue"


def block_text(out, a) -> str:
    return f"{a['id']} blocked: {a['reason']}"


def unblock_text(out, a) -> str:
    if out.exit == NOTHING:
        return out.reason
    released = out.data["released"]
    return f"released {len(released)} item(s): {', '.join(released[:MAX_LISTED_FILES])}" + (
        " ..." if len(released) > MAX_LISTED_FILES else ""
    )


# -- gate ------------------------------------------------------------------------------


def gate_text(out, a) -> str:
    """`gate status` and `gate list`: the prose the operation built."""
    return out.data["text"]


def gate_listed(out) -> bool:
    return out.exit != FAIL


def verify_notes(out, a, ctx) -> Iterator[str]:
    if ctx.json:
        return
    if out.data.get("reason"):
        yield out.data["reason"]
    elif not out.data.get("verified"):
        yield f"\n{out.reason}"


def verify_text(out, a) -> str | None:
    if out.data.get("reason"):
        return None
    lines = []
    for r in out.data.get("results", []):
        mark = "OK  " if (r["detected"] and r["applied"]) else "FAIL"
        lines.append(f"  {mark} {r['file']}: {'detected' if r['detected'] else r['detail']}")
    if out.data.get("verified"):
        lines.append(f"\n{a['gate']} CAN fail: every registered mutation was caught.")
    return "\n".join(lines) if lines else None


def run_shown(out) -> bool:
    """A command gate that ran has an outcome to show, whatever the exit; a gate that is
    a person's or an agent's, or one that was refused, has only its instruction."""
    return out.exit != REFUSED and (bool(out.data.get("outcome")) or out.exit == OK)


def run_text(out, a) -> str:
    ev = out.data.get("evidence") or {}
    head = f"{a['gate']}: {out.data['outcome'].upper()}" + (
        f" — {ev['reason']}" if ev.get("reason") else ""
    )
    tail = ev.get("tail", "")
    return f"{tail[-1200:]}\n{head}" if tail else head


def record_notes(out, a, ctx) -> Iterator[str]:
    if out.exit == OK and out.data.get("warning"):
        yield out.data["warning"]


def record_text(out, a) -> str:
    return f"{a['id']}.{a['gate']} = {out.data['outcome']}"
