"""PR flow: review threads.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import State
from ...infra import forge as FG


@dataclass
class ThreadsReport:
    number: int = 0
    url: str = ""
    threads: list[dict[str, Any]] = field(default_factory=list)
    replied: bool = False
    resolved: bool = False
    refused: str = ""
    unavailable: str = ""


def review_threads(
    repo: Path,
    cfg: Config,
    st: State,
    item: str,
    *,
    thread: str = "",
    reply: str = "",
    resolve: bool = False,
) -> ThreadsReport:
    """An item's review threads, read from the forge; optionally reply on one and/or
    resolve it -- so the reviewer sees which comments were addressed (B176).

    Read live, never from the log: a thread is the forge's, and the log is a snapshot.
    Replying and resolving are the forge's own writes, made as the operator who is logged
    in -- ddflow adds no identity of its own.
    """
    rep = ThreadsReport()
    it = st.items.get(item)
    if it is None or it.pr is None or not it.pr.number:
        rep.refused = f"{item} has no pull request: nothing to read threads from"
        return rep
    rep.number, rep.url = it.pr.number, it.pr.url
    if (reply or resolve) and not thread:
        rep.refused = "--reply and --resolve act on one thread: name it with --thread ID"
        return rep
    if reply and not reply.strip():
        rep.refused = "an empty reply says nothing"
        return rep
    try:
        forge = FG.detect(repo, cfg)
        found = forge.threads(it.pr.number)
        if thread and thread not in {t.id for t in found}:
            ids = ", ".join(t.id for t in found[:10]) or "none"
            rep.refused = f"no thread {thread!r} on #{it.pr.number} (threads: {ids})"
            rep.threads = [t.as_dict() for t in found]
            return rep
        if reply:
            forge.reply(it.pr.number, thread, reply)
            rep.replied = True
        if resolve:
            forge.resolve(it.pr.number, thread)
            rep.resolved = True
        if reply or resolve:
            found = forge.threads(it.pr.number)
    except FG.ForgeUnavailable as exc:
        rep.unavailable = str(exc) + _already(rep)
        return rep
    except FG.ForgeError as exc:
        rep.refused = str(exc) + _already(rep)
        return rep
    rep.threads = [t.as_dict() for t in found]
    return rep


def _already(rep: ThreadsReport) -> str:
    """What the forge already took before a later step failed: a reply is public and is
    not idempotent, so a failure after it must not read as "nothing happened"."""
    done = [
        w
        for w, ok in (
            ("the reply was posted", rep.replied),
            ("the thread was resolved", rep.resolved),
        )
        if ok
    ]
    return f" -- note: {' and '.join(done)} before this failed; do not repeat it" if done else ""
