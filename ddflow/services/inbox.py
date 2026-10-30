"""A per-lane inbox: what can wake an agent that is waiting, or tell one that is busy.

A wait is a fact about a running process on this machine, so the inbox lives under
`.ddflow/local/inbox/` (gitignored) beside the waiter registry, not in the event log. One
file per message in one directory per receiving agent: a sender never rewrites anything a
receiver reads, so there is no lock, and a torn write is a file the reader skips.

A message has a REASON -- why the receiver is being woken -- from `REASONS`:

* ``ready``       something the receiver was waiting for can start now;
* ``merge_offer`` another lane offers to finish this lane's item at the merge queue;
* ``yield``       the receiver holds files someone needs;
* ``message``     free text from the coordinator or another lane.

Messages are UNTRUSTED data (the sender may be any agent, and its text may be anything).
They are size-capped on the way in and FENCED on the way out (`fenced`), so an agent that
reads one is told it is data, never an instruction, and cannot be made to see the end of the
fence early. Delivery is `take`: read, then delete; a message is delivered once.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.slug import safe_filename
from ..infra import fsio
from ..infra.tomlcfg import atomic_write

INBOX_DIR = Path(".ddflow") / "local" / "inbox"
_CUT = " [cut]"
READ_CAP = 1 << 16  #: the most a reader keeps of one message, whatever a sender asked
REASONS = ("ready", "merge_offer", "yield", "message")

#: Control characters other than tab and newline: stripped, so a message cannot move a
#: cursor, clear a screen or hide text from a person who reads the transcript.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class Message:
    to: str
    reason: str = "message"
    text: str = ""
    sender: str = ""
    item: str = ""  #: the item it concerns, if any
    sent_at: float = 0.0
    id: str = ""

    def wire(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "to"}


def _dir(repo: Path, agent: str) -> Path:
    name = safe_filename(agent, repl="_", unicode=True, max=80)
    if not name.strip("."):  # "." and ".." name a directory, not an agent
        name = "_" + name
    return Path(repo) / INBOX_DIR / name


def clean(text: str, max_chars: int) -> tuple[str, bool]:
    """(the text without control characters, cut to ``max_chars``; whether it was cut)."""
    text = _CONTROL.sub("", str(text))
    return text[: max(0, max_chars)], len(text) > max_chars


def send(
    repo: Path,
    to: str,
    text: str = "",
    *,
    reason: str = "message",
    sender: str = "",
    item: str = "",
    max_chars: int = 2000,
    max_pending: int = 50,
    ttl_s: float = 0.0,
) -> tuple[Message | None, str]:
    """Put a message in ``to``'s inbox. (the message, why not): a refusal says why and
    stores nothing. A full inbox refuses rather than dropping an older message."""
    if not to or not to.strip():
        return None, "a message needs a receiver (--to AGENT)"
    if reason not in REASONS:
        return None, f"reason must be one of {', '.join(REASONS)}"
    d = _dir(repo, to)
    if len(pending(repo, to, ttl_s=ttl_s)) >= max_pending:
        return None, f"{to}'s inbox holds {max_pending} messages already: it is not reading them"
    max_chars = min(max_chars, READ_CAP)  # what a reader keeps whole
    body, cut = clean(text, max_chars)
    if cut and max_chars >= len(_CUT):  # the marker is inside the cap, never past it
        body = body[: max_chars - len(_CUT)] + _CUT
    now = time.time()
    msg = Message(
        to=to,
        reason=reason,
        text=body,
        sender=clean(sender, 80)[0],
        item=clean(item, 120)[0],
        sent_at=now,
        id=f"{now:.6f}-{clock.run_stamp()}",
    )
    try:
        d.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            fsio.ensure_ignored_dir(d.parent.parent)
        atomic_write(d / f"{safe_filename(msg.id, repl='_')}.json", json.dumps(asdict(msg)))
    except OSError as exc:
        return None, f"the inbox could not be written: {exc}"
    return msg, ""


def pending(repo: Path, agent: str, *, ttl_s: float = 0.0) -> list[Message]:
    """``agent``'s undelivered messages, oldest first. Torn or foreign files are skipped;
    one older than ``ttl_s`` (0 = never) is dropped, so a lane that never reads cannot hold
    an inbox forever."""
    d = _dir(repo, agent)
    if not d.is_dir():
        return []
    out: list[tuple[Path, Message]] = []
    now = time.time()
    for path in sorted(d.glob("*.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
            m = Message(**{k: v for k, v in raw.items() if k in Message.__dataclass_fields__})
            ok = all(isinstance(getattr(m, f), str) for f in ("to", "reason", "text", "sender"))
            ok = ok and isinstance(m.sent_at, (int, float)) and m.reason in REASONS
            ok = ok and isinstance(m.item, str) and not isinstance(m.sent_at, bool)
            ok = ok and math.isfinite(m.sent_at)
            m.id = path.stem  # the file is the identity: a forged id cannot hide a message
            # Whatever the file holds is read as untrusted, however it got there.
            if ok:
                m.to, m.sender, m.item = (
                    clean(m.to, 80)[0],
                    clean(m.sender, 80)[0],
                    clean(m.item, 120)[0],
                )
                m.text = clean(m.text, READ_CAP)[0]
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            continue
        if not ok:
            continue
        if ttl_s > 0 and now - m.sent_at > ttl_s:
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        out.append((path, m))
    return [m for _p, m in sorted(out, key=lambda pm: (pm[1].sent_at, pm[0].name))]


def take(repo: Path, agent: str, *, ttl_s: float = 0.0) -> list[Message]:
    """Deliver: the pending messages, each removed as it is returned."""
    got = pending(repo, agent, ttl_s=ttl_s)
    for m in got:
        with contextlib.suppress(OSError):
            (_dir(repo, agent) / f"{safe_filename(m.id, repl='_')}.json").unlink()
    return got


def fenced(m: Message) -> str:
    """The message as an agent is shown it: marked as untrusted data inside a fence the text
    itself cannot close (any fence-looking run in it is broken)."""
    safe = m.text.replace("<ddflow-message", "<\u200bddflow-message").replace(
        "</ddflow-message", "<\u200b/ddflow-message"
    )
    head = f'<ddflow-message reason="{m.reason}" from="{_attr(m.sender)}" trust="untrusted"'
    if m.item:
        head += f' item="{_attr(m.item)}"'
    return f"{head}>\n{safe}\n</ddflow-message>"


def _attr(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:/@ -]", "_", v)


def wake_reason(msgs: list[Message]) -> str:
    """The reason that wakes: the most urgent of the messages (yield, then merge_offer,
    ready, message), "" for none."""
    order = ("yield", "merge_offer", "ready", "message")
    have = {m.reason for m in msgs}
    return next((r for r in order if r in have), "")


def view(msgs: list[Message]) -> list[dict[str, Any]]:
    """Wire form for a result: each message with ONLY its fenced text (never the raw one)."""
    return [
        {**{k: v for k, v in m.wire().items() if k != "text"}, "fenced": fenced(m)} for m in msgs
    ]


def wake(
    repo: Path, agent: str, *, ttl_s: float = 0.0, consume: bool = True
) -> dict[str, Any] | None:
    """``agent``'s inbox as a wake: {"reason", "messages"} (messages fenced), or None when it
    is empty. The one door `wait`, `watch` and `heartbeat` all use. ``consume=False`` only
    shows what is pending (release and complete: the agent is leaving, and the offer stays
    for the lane's next wait)."""
    msgs = take(repo, agent, ttl_s=ttl_s) if consume else pending(repo, agent, ttl_s=ttl_s)
    if not msgs:
        return None
    return {"reason": wake_reason(msgs), "messages": view(msgs)}


def has_pending(repo: Path, agent: str, *, ttl_s: float = 0.0) -> bool:
    """Cheap test used in a wait's poll loop: is anything waiting to be delivered? With a
    ``ttl_s`` an expired message does not count (it is dropped), so a wait never wakes for
    a message that delivery would then discard."""
    return bool(pending(repo, agent, ttl_s=ttl_s))
