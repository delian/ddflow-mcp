"""`ddflow digest`: the operator queue, one fold (B-digest-route-names-missing-command).

The queue (`services/digest/queue.build`) was built with no surface: no module outside
`services/digest` imported it, so the operator queue had neither a CLI verb nor an MCP
tool, and the source table's ``ASK`` chat route named a command (`ddflow digest --ask`)
that did not exist. This is that surface: it folds the log once and renders what waits
on a person, ranked the way the queue ranks it, with the command that answers each entry
and the chat route that puts it to the person.

Read-only: nothing here writes an event or answers anything.
"""

from __future__ import annotations

import time
from pathlib import Path

from ...core import outcome as O
from ...services.digest import queue as DQ
from .._base import _load

OWNER_ORDER = ("operator", "lead")


def _render_entry(e) -> list[str]:
    age = int(e.age_s)
    head = f"- [{e.severity.upper()}] {e.id}  {e.what}"
    if e.impact:
        head += f"  (blocks {e.impact})"
    lines = [
        head,
        f"    why: {e.why}",
        f"    since: {e.since}  ({age}s ago)" if e.since else f"    age: {age}s",
    ]
    if e.default:
        lines.append(f"    default: {e.default}")
    if e.answer:
        lines.append(f"    answer: {e.answer}")
    if e.command:
        lines.append(f"    command: {e.command}")
    if e.chat:
        lines.append(f"    chat: {e.chat}")
    return lines


def _render(queue: DQ.Queue, ask: str) -> tuple[str, list[dict]]:
    """Return ``(text, entry_dicts)``: the shared rendering so both surfaces agree.

    The entry dicts are the machine view (the ``entries`` payload); ``text`` is the human
    view built from the same objects (the B37 shape ``vigil`` and ``board`` are built to).
    """
    if ask:
        entry = next((e for e in queue.entries if e.id == ask), None)
        if entry is None:
            return "", []
        return (
            "\n".join(["# ask: " + ask, entry.what, *(f"{k}: {v}" for k, v in (
                ("why", entry.why), ("default", entry.default), ("answer", entry.answer),
                ("command", entry.command), ("chat", entry.chat),
            ) if v)]) + "\n",
            [entry.as_dict()],
        )
    rows = [e.as_dict() for e in queue.entries]
    lines = ["# operator queue", ""]
    if not rows:
        lines.append("nothing waits on a person")
    for owner in OWNER_ORDER:
        owned = [e for e in queue.entries if e.owner == owner]
        if not owned:
            continue
        lines.append(f"## {owner} ({len(owned)})")
        for e in owned:
            lines.extend(_render_entry(e))
        lines.append("")
    if queue.gaps:
        lines.append("gaps:")
        lines.extend(f"  {g}" for g in queue.gaps)
    if queue.notes:
        lines.append("notes:")
        lines.extend(f"  {n}" for n in queue.notes)
    return "\n".join(lines) + "\n", rows


def digest_queue(repo: Path, *, ask: str = "", agent: str = "") -> O.Outcome:
    """The operator queue, or (``ask``) the one entry to put to the person."""
    _log, cfg, st = _load(repo, agent)
    queue = DQ.build(st, cfg, time.time())
    text, rows = _render(queue, ask if ask else "")
    if ask and not rows:
        return O.nothing(
            "digest",
            f"no queue entry {ask!r}; run `ddflow digest` for the queue",
            id=ask,
        )
    return O.ok("digest", entries=rows, gaps=list(queue.gaps), notes=list(queue.notes), text=text)
