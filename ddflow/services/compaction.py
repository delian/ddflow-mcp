"""What a session was doing when its context was compacted (B195).

Claude Code's PreCompact hook gets no summary -- only `session_id`, `transcript_path`
and `trigger` (manual | auto); the summary is made after compaction and no hook receives
it (code.claude.com/docs/en/hooks). So the record is a bounded digest of the transcript's
tail: the operator's last words and the agent's last answers, tool traffic left out,
secrets redacted like a recorded prompt, written as a session note with
`source = compaction:<trigger>` in the session the prompt hook uses. After compaction the
SessionStart hook hands the digest back to the agent, so what it was doing survives.

Nothing here may block or fail a compaction: every problem is a reason, never a raise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..infra.log import EventLog
from . import sessions as S

SOURCE = "compaction"
#: How far back a compaction note counts as this compaction's, for the SessionStart check.
RECENT_S = 900


def _text_of(message: Any) -> str:
    """The human-readable text of a transcript message: strings and `text` parts only."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        )
    return ""


def digest(transcript: Path, budget: int) -> str:
    """The transcript's last turns within `budget` characters, oldest first:
    `operator:` / `agent:` lines; tool calls, tool results and meta entries left out."""
    try:
        lines = Path(transcript).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    turns: list[str] = []
    used = 0
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isMeta"):
            continue
        who = {"user": "operator", "assistant": "agent"}.get(str(entry.get("type")))
        text = " ".join(_text_of(entry.get("message")).split()) if who else ""
        if not text:
            continue  # a tool result arrives as a user entry with no text part
        line = f"{who}: {text}"
        if used + len(line) > budget:
            keep = budget - len(who) - 5  # room after "<who>: ..."
            if not turns and keep > 0:  # the last turn alone is over budget: keep its end
                turns.append(f"{who}: ...{text[-keep:]}")
            break
        turns.append(line)
        used += len(line) + 1
    return "\n".join(reversed(turns))


def record(
    log: EventLog,
    cfg: Config,
    payload: dict[str, Any],
    held: list[str],
    known: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Write the digest as a session note; returns what happened ("recorded", or why not).
    ``known`` is the session ids the caller's fold already holds."""
    budget = cfg.session.compaction_digest_chars
    if budget <= 0:
        return "off"
    path = payload.get("transcript_path")
    body = digest(Path(path), budget) if isinstance(path, str) and path else ""
    trigger = str(payload.get("trigger") or "auto")
    head = f"Context compaction ({trigger})" + (f"; holding {', '.join(held)}" if held else "")
    if not body and not held:
        return "nothing to record (no transcript text)"
    sid = S.harness_session_id(str(payload.get("session_id") or "")) or S.resolve(log)[0]
    if sid not in known:  # the caller's fold: the log is not read a second time
        log.append("session.started", sid, {"tool": "hook", "cwd": str(Path.cwd())})
    clean, _ = S.redact(f"{head}. Last turns before it:\n{body}" if body else head, cfg)
    log.append("session.note", sid, {"text": clean, "item": "", "source": f"{SOURCE}:{trigger}"})
    return "recorded"


def latest(log: EventLog, harness_id: str, *, within_s: float = RECENT_S) -> str:
    """The text of this harness session's most recent compaction note, if it is recent."""
    import time

    sid = S.harness_session_id(harness_id)
    if not sid:
        return ""
    for ev in reversed(log.read_all()):
        if ev.kind != "session.note" or ev.subject != sid:
            continue
        if not str(ev.data.get("source", "")).startswith(SOURCE):
            continue
        try:
            at = clock.parse_ts(ev.ts, naive="local").timestamp()
        except clock.UNPARSEABLE:
            return ""
        return str(ev.data.get("text", "")) if time.time() - at <= within_s else ""
    return ""
