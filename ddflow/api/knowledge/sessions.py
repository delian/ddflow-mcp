"""Session records: start, the operator's prompts, notes, end, and the history view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import csv_list
from ...core import outcome as O
from ...services import sessions as S
from .._base import _load


def session_start(repo: Path, *, model: str = "", tool: str = "", agent: str = "") -> O.Outcome:

    log, cfg, _st = _load(repo, agent)
    return O.ok("session.started", session=S.start(log, cfg, model=model, agent_tool=tool))


#: Why an empty session record is refused rather than written.
_EMPTY_SESSION_TEXT = (
    "refusing an empty {what}: the text is empty or whitespace-only, and nothing was "
    "recorded. Pass the words with --text (or pipe them on stdin)."
)


def session_prompt(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:
    """Record the operator's own words, with credentials redacted before they touch disk.

    Empty or whitespace-only text is refused, recording nothing: an empty prompt in the
    log is a hole `ddflow replay` cannot see as one. A missing session id is not a
    reason to refuse: the latest open session is used, else an implicit one is opened,
    and `how` says which.
    """

    if not (text or "").strip():
        return O.failed(
            "session.prompt", _EMPTY_SESSION_TEXT.format(what="prompt"), session=session
        )
    log, cfg, _st = _load(repo, agent)
    if not cfg.session.log_prompts:
        # Nothing is recorded, so no session is opened for it either.
        return O.ok("session.prompt", redactions=0, session=session, how="off")
    sid, how = S.resolve(log, session)
    return O.ok(
        "session.prompt", redactions=S.prompt(log, cfg, sid, text, item=item), session=sid, how=how
    )


def session_note(
    repo: Path, session: str, text: str, *, item: str = "", agent: str = ""
) -> O.Outcome:

    if not (text or "").strip():
        return O.failed("session.note", _EMPTY_SESSION_TEXT.format(what="note"), session=session)
    log, cfg, _st = _load(repo, agent)
    sid, how = S.resolve(log, session)
    S.note(log, cfg, sid, text, item=item)
    return O.ok("session.note", session=sid, how=how)


def session_adopt_orphans(repo: Path, *, agent: str = "") -> O.Outcome:
    """Attach prompts and notes recorded with no session id to the nearest session."""

    log, _cfg, _st = _load(repo, agent)
    return O.ok("session.adopted", adopted=S.adopt_orphans(log))


def session_end(repo: Path, session: str, *, summary: str = "", agent: str = "") -> O.Outcome:

    log, _cfg, _st = _load(repo, agent)
    S.end(log, session, summary=summary)
    return O.ok("session.ended", session=session)


def history(
    repo: Path,
    *,
    item: str = "",
    kind: str = "",
    since: str = "",
    limit: int = 40,
    agent: str = "",
    by_agent: str = "",
    tail: int = 0,
) -> O.Outcome:
    """One reverse-chronological timeline of everything that happened.

    `by_agent` keeps one agent's shard only (`agent` is the CALLER's identity, not a
    filter). `tail=N` is the last N events oldest-first, like `tail`, and overrides `limit`.

    Ordered by `(lamport, agent, id)` like everything else — NOT by wall-clock timestamp.
    Two agents on two machines have two clocks, and sorting a merged history by `ts` would
    interleave them wrongly while looking perfectly plausible.
    """
    log, _cfg, _st = _load(repo, agent)
    events = log.read_all()
    if item:
        events = [e for e in events if e.subject == item]
    if kind:
        wanted = set(csv_list(kind))
        events = [e for e in events if e.kind in wanted or e.kind.split(".")[0] in wanted]
    if since:
        events = [e for e in events if e.ts >= since]
    if by_agent:
        events = [e for e in events if e.agent == by_agent]
    events = sorted(events, key=lambda e: (e.lamport, e.agent, e.id), reverse=True)
    shown = events[:tail][::-1] if tail and tail > 0 else events[:limit]
    data: dict[str, Any] = {
        "total": len(events),
        "shown": len(shown),
        "events": [
            {
                "id": e.id,
                "at": e.ts,
                "lamport": e.lamport,
                "agent": e.agent,
                "kind": e.kind,
                "subject": e.subject,
                "data": e.data,
            }
            for e in shown
        ],
        "_render": {"events": shown, "total": len(events)},
    }
    if not shown:
        return O.nothing("history", "Nothing in the history matches.", **data)
    return O.ok("history", **data)
