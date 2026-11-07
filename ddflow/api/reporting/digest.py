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


def _render_ask(queue: DQ.Queue, ask: str) -> tuple[str, list[dict]]:
    """``(text, entry_dict)`` for the one entry to put to the person."""
    entry = next((e for e in queue.entries if e.id == ask), None)
    if entry is None:
        return "", []
    fields = (
        ("why", entry.why), ("default", entry.default), ("answer", entry.answer),
        ("command", entry.command), ("chat", entry.chat),
    )
    lines = ["# ask: " + ask, entry.what]
    lines.extend(f"{k}: {v}" for k, v in fields if v)
    return "\n".join(lines) + "\n", [entry.as_dict()]


def _render_queue(queue: DQ.Queue) -> tuple[str, list[dict]]:
    rows = [e.as_dict() for e in queue.entries]
    lines = ["# operator queue", ""]
    if not rows:
        lines.append("nothing waits on a person")
    for owner in OWNER_ORDER:
        owned = [e for e in queue.entries if e.owner == owner]
        if not owned:
            continue
        lines.append(f"## {owner} ({len(owned)})")
        lines.extend(line for e in owned for line in _render_entry(e))
        lines.append("")
    for label, items in (("gaps", queue.gaps), ("notes", queue.notes)):
        if items:
            lines.append(label + ":")
            lines.extend(f"  {i}" for i in items)
    return "\n".join(lines) + "\n", rows


def _render(queue: DQ.Queue, ask: str) -> tuple[str, list[dict]]:
    """Return ``(text, entry_dicts)``: the shared rendering so both surfaces agree.

    The entry dicts are the machine view (the ``entries`` payload); ``text`` is the human
    view built from the same objects (the B37 shape ``vigil`` and ``board`` are built to).
    """
    return _render_ask(queue, ask) if ask else _render_queue(queue)


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
