"""Recording a session: the prompt, the note and the session lifecycle.

Holds redaction on the way into the committed log (`redact`), session ids, the
double-fire window that keeps a hook's capture and an agent's own record of one prompt
from landing twice, resolving which session a write belongs to, adopting orphans, and
`prompt` / `capture_prompt` / `note` / `start` / `end`. Redaction is applied on the way
in, not on the way out: the log is committed, so a secret written once is leaked for good.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path

from ...config import Config
from ...core import clock
from ...core import ids as IDS
from ...core.digest import content_digest
from ...core.redact_errors import PatternError, fail_closed_text
from ...core.slug import safe_filename
from ...infra.log import EventLog
from .. import harnessreg as HR
from ..jobs import proc_start
from ..redact_report import redactor


def redact(text: str, cfg: Config) -> tuple[str, int]:
    """Redact for the committed log (the `log` profile). Returns (clean_text, n_redactions).

    **An invalid pattern raises rather than being skipped.** This is a security control
    writing to a COMMITTED log, and its failure mode is silence: see `core.redact`.
    """
    try:
        red = redactor("log", cfg).text(text)
    except PatternError:
        raise
    except Exception:  # fail closed: the placeholder is written, never the raw text
        return fail_closed_text()
    return red.text, red.total


def new_session_id(cfg: Config | None = None, events: Iterable = ()) -> str:
    """A new session's id, from `[ids].session` (`s<UTC time>-<pid>` by default) through
    the id service, checked against the sessions ``events`` already started."""
    used = {e.subject: "session" for e in events if e.kind == "session.started"}
    return IDS.make(cfg if cfg is not None else Config(), "session", used=used).id


def _cfg_of(log: EventLog) -> Config:
    """The project's config, for an id minted where only the log is at hand; the shipped
    defaults when it cannot be read (a session id must never fail to mint)."""
    try:
        return Config.load(log.root)
    except Exception:
        return Config()


def start(
    log: EventLog, cfg: Config, *, model: str = "", agent_tool: str = "", session_id: str = ""
) -> str:
    sid = session_id or new_session_id(cfg, log.read_all())
    log.append("session.started", sid, {"model": model, "tool": agent_tool, "cwd": str(Path.cwd())})
    return sid


#: How long an automatically captured prompt and an agent's own record of the same words
#: count as one prompt. The hook fires before the agent's turn, so the agent's call is the
#: second writer; the window only has to span one turn.
DEDUPE_WINDOW_S = 120
#: A hook firing twice for one prompt lands within moments; a longer window would eat an
#: operator who really types "continue" twice.
DOUBLE_FIRE_S = 3
#: The most of a hook's own startup the double-fire window forgives. A process older than
#: this is no freshly fired hook (an in-process API caller), and its start says nothing
#: about when the harness fired.
HOOK_START_MAX_S = 30


def _age_s(ts: str, now: float | None = None) -> float:
    """Seconds from `ts` to `now` (default: the current time); infinite when `ts` is not a
    timestamp. Any ISO 8601 shape, so `...:00Z` without microseconds is as young as
    `...:00.000000Z` (Bbf85f6576f)."""
    # Not `progress.epoch`: its 0.0 failure value is also the epoch instant, and it reads
    # a time with no zone as host-local. Here a zone-less time is UTC, as the log writes it.
    return clock.age_s(ts, now, naive="utc")


def process_started_at() -> float:
    """When this process started, as wall-clock time; the current time where the
    platform cannot say.

    A hook is fired when its process starts. Everything after that -- the interpreter,
    the imports, the config, the log read -- is this process's own delay, which a loaded
    machine stretches past any short window (B72b9ab33fc). Linux keeps the start in
    /proc on the boot clock; its distance from the boot clock now is the process's age.
    """
    try:
        ticks = int(proc_start(os.getpid()))  # "" where there is no /proc
        age = time.clock_gettime(time.CLOCK_BOOTTIME) - ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, AttributeError):
        return time.time()
    return time.time() - max(0.0, age)


def _recorded_recently(
    log: EventLog,
    clean: str,
    *,
    window: float = DEDUPE_WINDOW_S,
    subject: str = "",
    auto_only: bool = False,
    now: float | None = None,
) -> bool:
    """True when `clean` was already recorded as a prompt inside the window before `now`
    (default: the current time).

    `subject` restricts the match to one session (a hook that fired twice for one
    conversation). `auto_only` restricts it to hook-captured prompts, for an AGENT's own
    record of words the hook already took. Known limit: an agent in a harness WITHOUT a
    hook that types words identical to another session's capture within the window is
    treated as the copy; the capture holds the same words, so no text is lost.
    """
    for ev in log.read_all():
        if ev.kind != "session.prompt" or ev.data.get("text") != clean:
            continue
        if _age_s(ev.ts, now) > window:
            continue
        if subject and ev.subject != subject:
            continue
        if auto_only and not ev.data.get("auto"):
            continue
        return True
    return False


def is_started(events: Iterable, session_id: str) -> bool:
    """Whether a session by this id was ever started (open or ended)."""
    return any(e.kind == "session.started" and e.subject == session_id for e in events)


def open_sessions(events: list, agent: str) -> list[str]:
    """Ids of this agent's sessions that have not ended, the most recently active first.

    Activity is the last event of any kind on the session, ordered by the Lamport clock
    (the wall clock breaks a tie), so a session someone is still writing to outranks one opened later and
    then abandoned. An ended session is never listed.
    """
    started: dict[str, bool] = {}
    ended: set[str] = set()
    last: dict[str, tuple[int, str]] = {}
    for ev in events:
        if not ev.kind.startswith("session.") or not ev.subject:
            continue
        if ev.kind == "session.started" and ev.agent == agent:
            started[ev.subject] = True
        elif ev.kind == "session.ended":
            ended.add(ev.subject)
        last[ev.subject] = max(last.get(ev.subject, (0, "")), (ev.lamport, ev.ts))
    live = [sid for sid in started if sid not in ended]
    return sorted(live, key=lambda sid: last.get(sid, (0, "")), reverse=True)


def resolve(
    log: EventLog, session_id: str = "", *, model: str = "", tool: str = ""
) -> tuple[str, str]:
    """The session a prompt or note belongs to, and how it was found.

    explicit (the caller's id) / harness (the session the prompt hook keys on, named by
    the environment) / latest (this agent's most recently active open session) / implicit
    (nothing open: a new session, marked implicit in its start event). Never empty.
    """
    if session_id.strip():
        return session_id.strip(), "explicit"
    events = log.read_all()
    live = open_sessions(events, log.agent_id)
    literal = os.environ.get("DDFLOW_SESSION_ID", "").strip()
    if literal and literal in live:
        return literal, "harness"
    for var in HR.session_env_vars():
        hid = harness_session_id(os.environ.get(var, ""))
        if hid and hid in live:
            return hid, "harness"
    if live:
        return live[0], "latest"
    sid = new_session_id(_cfg_of(log), events)
    log.append(
        "session.started",
        sid,
        {
            "model": model,
            "tool": tool or HR.detect(os.environ),
            "cwd": str(Path.cwd()),
            "implicit": True,
        },
    )
    return sid, "implicit"


def orphans(events: list) -> list:
    """Prompts and notes recorded with no session id."""
    return [
        e for e in events if e.kind in ("session.prompt", "session.note") and not e.subject.strip()
    ]


def unadopted_orphans(events: list) -> list:
    """The orphans no copy carrying `adopted_from` has adopted yet, in log order."""
    done = {e.data.get("adopted_from") for e in events if e.data.get("adopted_from")}
    return [o for o in orphans(events) if o.id not in done]


def plan_adoptions(events: list, cfg: Config | None = None) -> list[tuple[str, str, dict]]:
    """The events that adopt every unadopted orphan, as `(kind, subject, data)`, in the
    order they must be appended -- pure, so `services.repairs` can show them before writing.

    Nearest is the session whose start is the latest one not after the orphan, else the
    earliest; with no session at all, one implicit session is opened first.
    """
    starts = sorted((e.ts, e.subject) for e in events if e.kind == "session.started" and e.subject)
    out: list[tuple[str, str, dict]] = []
    for o in unadopted_orphans(events):
        if starts:
            before = [sid for ts, sid in starts if ts <= o.ts]
            sid = before[-1] if before else starts[0][1]
        else:
            sid = new_session_id(cfg, events)
            out.append(("session.started", sid, {"model": "", "tool": "", "implicit": True}))
            starts = [(o.ts, sid)]
        out.append((o.kind, sid, {**o.data, "adopted_from": o.id, "orphan_at": o.ts}))
    return out


def adopt_orphans(log: EventLog) -> int:
    """Attach each orphan to the nearest session by time; returns how many were attached.

    The log is append-only, so the orphan stays and a copy carrying `adopted_from` is
    written under the session. A copy already written is not written again
    (`plan_adoptions` says where each goes).
    """
    planned = plan_adoptions(log.read_all(), _cfg_of(log))
    for kind, subject, data in planned:
        log.append(kind, subject, data)
    return sum(1 for _k, _s, d in planned if "adopted_from" in d)


def prompt(log: EventLog, cfg: Config, session_id: str, text: str, *, item: str = "") -> int:
    """Record one operator prompt verbatim (after redaction). Returns redaction count.

    Not written again when the prompt hook already captured the same words a moment ago.
    """
    if not cfg.session.log_prompts:
        return 0
    clean, n = redact(text, cfg)
    if _recorded_recently(log, clean, auto_only=True):
        return n
    log.append("session.prompt", session_id, {"text": clean, "item": item, "redactions": n})
    return n


def harness_session_id(raw: str) -> str:
    """The ddflow session id for a harness's own session id (one session per conversation)."""
    raw = str(raw)
    safe = safe_filename(raw, repl="", max=48)
    if not safe:
        return ""
    if safe != raw:
        # Sanitising or truncating made distinct ids collide; a digest keeps them apart.

        safe += "-" + content_digest(raw, length=16)
    return f"h-{safe}"


def capture_prompt(
    log: EventLog,
    cfg: Config,
    harness_id: str,
    text: str,
    *,
    model: str = "",
    tool: str = "",
    fired_at: float | None = None,
) -> str:
    """Record a prompt a harness hook delivered. Returns what happened, never raises on
    an absent id or empty text.

    `fired_at` is when the harness fired the hook (default: now): a second firing is a
    copy when it was FIRED within `DOUBLE_FIRE_S` of the first's record, however long
    (up to `HOOK_START_MAX_S`) it then took to get here.

    The text is redacted BEFORE any event is built, so a secret never reaches the log; a
    session is opened once per harness session id and reused after that.
    """
    if not cfg.session.log_prompts:
        return "off"
    if not (text or "").strip():
        return "empty"
    sid = harness_session_id(harness_id) or resolve(log, model=model, tool=tool)[0]
    clean, n = redact(text, cfg)
    events = log.read_all()
    if not is_started(events, sid):
        log.append("session.started", sid, {"model": model, "tool": tool, "cwd": str(Path.cwd())})
    now = time.time()
    fired = now if fired_at is None else min(now, max(fired_at, now - HOOK_START_MAX_S))
    if _recorded_recently(log, clean, window=DOUBLE_FIRE_S, subject=sid, now=fired):
        return "duplicate"
    log.append("session.prompt", sid, {"text": clean, "item": "", "redactions": n, "auto": True})
    return "recorded"


def note(log: EventLog, cfg: Config, session_id: str, text: str, *, item: str = "") -> None:
    clean, _ = redact(text, cfg)
    log.append("session.note", session_id, {"text": clean, "item": item})


def end(log: EventLog, session_id: str, *, summary: str = "") -> None:
    log.append("session.ended", session_id, {"summary": summary})
