"""Sessions, prompt provenance, and reconstruction from the log alone.

The requirement this module serves is unusual and worth stating plainly: *if
everything except the log were lost, could the project be rebuilt?* Not byte-for-byte
— an LLM is not a deterministic function, so replaying prompts cannot reproduce the
same source. What CAN be reproduced is the **decision history**: every operator
instruction, in order, with the state it applied to, the research that informed it, the
gates it passed and the commit it produced.

So ddflow records two layers, and keeps them separate on purpose:

* **Intent** — operator prompts, research verdicts, lessons, the queue's shape. This
  is irreplaceable; no artefact elsewhere contains it. It is what ``replay`` emits.
* **Outcome** — commit shas, gate evidence, output digests. This is *verification*
  data: it cannot rebuild anything, but it proves whether a rebuild matches what
  happened, and ``replay --verify`` checks each recorded sha still resolves.

The literature calls this an event-sourced agent: the log is the agent, the working
state is a projection, and replay reconstructs a run by folding forward rather than by
restoring a snapshot (Sanders et al., *The Log is the Agent*, arXiv:2605.21997).
The determinism caveat is theirs too — replay is made sound by recording responses,
not by assuming they reproduce.

**Redaction is applied on the way in, not on the way out.** The log is committed to
git, so a secret written once is a secret leaked permanently; scrubbing at read time
would be a scrub that a `git show` walks straight past.
"""

from __future__ import annotations

import json
import os
import textwrap
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core import clock
from ..core.events import OLDER_MARK
from ..core.model import ADD_RELATIONS, State, link_targets
from ..core.slug import safe_filename
from ..infra.log import PROVENANCE_KINDS, Event, EventLog
from .redact_report import redactor


def redact(text: str, cfg: Config) -> tuple[str, int]:
    """Redact for the committed log (the `log` profile). Returns (clean_text, n_redactions).

    **An invalid pattern raises rather than being skipped.** This is a security control
    writing to a COMMITTED log, and its failure mode is silence: see `core.redact`.
    """
    red = redactor("log", cfg).text(text)
    return red.text, red.total


def new_session_id(cfg: Config | None = None, events: Iterable = ()) -> str:
    """A new session's id, from `[ids].session` (`s<UTC time>-<pid>` by default) through
    the id service, checked against the sessions ``events`` already started."""
    from ..core import ids as IDS

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
    from .jobs import proc_start

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
    for var in ("DDFLOW_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        hid = harness_session_id(os.environ.get(var, ""))
        if hid and hid in live:
            return hid, "harness"
    if live:
        return live[0], "latest"
    sid = new_session_id(_cfg_of(log), events)
    log.append(
        "session.started",
        sid,
        {"model": model, "tool": tool, "cwd": str(Path.cwd()), "implicit": True},
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
        from ..core.digest import content_digest

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
    if not any(e.kind == "session.started" and e.subject == sid for e in events):
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


# -- reconstruction -------------------------------------------------------------------


@dataclass
class ReplayStep:
    n: int
    at: str
    kind: str
    text: str
    item: str = ""
    verdict: str = ""
    sha: str = ""
    #: The version of the OLDER ddflow that wrote this event under a skew override ("" when
    #: it was not), so a reconstruction says which steps came from a stale tool.
    older: str = ""


def replay(events: list[Event], *, include_outcomes: bool = True) -> list[ReplayStep]:
    """The ordered intent history — the input to a from-scratch rebuild.

    Filters the log to the kinds that carry irreplaceable information. Gate evidence
    and lease churn are deliberately excluded: they describe *how the work was checked*,
    not *what was asked for*, and including them buries the twenty sentences that
    matter under ten thousand that do not.
    """
    steps: list[ReplayStep] = []
    n = 0
    # An adopted orphan lives on as the copy under its session; the id-less original
    # would read as the same words twice.
    adopted = {e.data["adopted_from"] for e in events if e.data.get("adopted_from")}
    for ev in events:
        if ev.id in adopted:
            continue
        if ev.kind not in PROVENANCE_KINDS and not (
            include_outcomes and ev.kind == "item.completed"
        ):
            continue
        n += 1
        step = _REPLAY_RENDERERS.get(ev.kind, lambda _n, _e: None)(n, ev)
        if step is not None:
            step.older = str(ev.data.get(OLDER_MARK, ""))
            steps.append(step)
    return steps


def _rs_skew_overridden(n, ev):
    d = ev.data
    return ReplayStep(
        n,
        ev.ts,
        "session",
        f"The operator let ddflow {d.get('running', '?')} write to a log ddflow "
        f"{d.get('log_version', '?')} has worked on (skew override): {d.get('reason', '')}",
    )


def _rs_upgrade_applied(n, ev):
    d = ev.data
    cats = ", ".join(d.get("categories") or []) or "nothing"
    backup = f"; originals saved in {d['backup']}" if d.get("backup") else ""
    confirmed = ", ".join(sorted(d.get("confirmed") or {}))
    sure = f"; the operator confirmed {confirmed}" if confirmed else ""
    return ReplayStep(
        n,
        ev.ts,
        "session",
        f"Upgraded the project from ddflow {d.get('from') or 'before version stamps'} to "
        f"{d.get('to') or '?'}: {cats} ({len(d.get('items') or [])} item(s)){sure}{backup}",
    )


def _rs_prompt(n, ev):
    return ReplayStep(n, ev.ts, "prompt", ev.data.get("text", ""), ev.data.get("item", ""))


def _rs_note(n, ev):
    return ReplayStep(n, ev.ts, "note", ev.data.get("text", ""), ev.data.get("item", ""))


def _rs_session_started(n, ev):
    model = ev.data.get("model") or "unknown model"
    return ReplayStep(n, ev.ts, "session", f"session {ev.subject} opened on {model}")


def _rs_session_ended(n, ev):
    return ReplayStep(
        n, ev.ts, "session", f"session {ev.subject} closed. {ev.data.get('summary', '')}"
    )


def _rs_phase(n, ev):
    d = ev.data
    text = f"{ev.subject}: {d.get('title', '')}" + _link_note(d)
    if d.get("body"):
        text += "\n\n" + d["body"].strip()
    return ReplayStep(n, ev.ts, "phase", text, ev.subject)


def _link_note(d: dict) -> str:
    """` [extends T1; related T2]` -- how an add said it relates to what was there."""
    parts = []
    for relation in ADD_RELATIONS:
        for t in link_targets(d.get(relation)):
            parts.append(f"{relation.replace('_', ' ')} {t}")
    return f" [{'; '.join(parts)}]" if parts else ""


def _rs_extended(n, ev):
    d = ev.data
    who = d.get("who") or ev.agent
    return ReplayStep(
        n, ev.ts, "addition", f"added to {ev.subject} by {who}:\n\n{d.get('text', '')}", ev.subject
    )


def _rs_link(n, ev):
    d = ev.data
    relation = d.get("relation", "") or "unspecified"
    verb = {"distinct": "is DISTINCT from"}.get(relation, relation)
    targets = ", ".join(link_targets(d.get("target"))) or "?"
    return ReplayStep(
        n, ev.ts, "link", f"{ev.subject} {verb.replace('_', ' ')} {targets}", ev.subject
    )


def _rs_task(n, ev):
    d = ev.data
    needs = ", ".join(d.get("needs", [])) or "-"
    text = (
        f"{ev.subject}: {d.get('title', '')} "
        f"(in {d.get('parent', '')}; needs {needs}"
        + (f"; writes {', '.join(d['globs'])}" if d.get("globs") else "")
        + ")"
    )
    text += _link_note(d)
    if d.get("body"):
        text += "\n\n" + d["body"].strip()
    return ReplayStep(n, ev.ts, "task", text, ev.subject)


def _rs_decision(n, ev):
    """The least recoverable thing in a project: source code shows WHAT was built and
    never why, nor what was rejected on the way there."""
    d = ev.data
    parts = [d.get("title", "")]
    for label, key in (
        ("Context", "context"),
        ("Decision", "decision"),
        ("Consequences", "consequences"),
        ("Rejected", "alternatives"),
    ):
        if d.get(key):
            parts.append(f"{label}: {d[key]}")
    if d.get("globs"):
        parts.append(f"Governs: {', '.join(d['globs'])}")
    return ReplayStep(n, ev.ts, "decision", "\n\n".join(parts) + _link_note(d), d.get("item", ""))


def _rs_decision_superseded(n, ev):
    d = ev.data
    return ReplayStep(
        n,
        ev.ts,
        "decision",
        f"{ev.subject} was SUPERSEDED by {d.get('by', '?')}"
        + (f": {d['reason']}" if d.get("reason") else ""),
    )


def _rs_research(n, ev):
    d = ev.data
    return ReplayStep(
        n,
        ev.ts,
        "research",
        f"{d.get('question', '')} -> {d.get('claim', '')}{_link_note(d)}",
        d.get("item", ""),
        verdict=d.get("verdict", ""),
    )


def _rs_lesson(n, ev):
    d = ev.data
    return ReplayStep(
        n, ev.ts, "lesson", f"{d.get('title', '')}: {d.get('rule', '')}{_link_note(d)}"
    )


def _rs_completed(n, ev):
    return ReplayStep(n, ev.ts, "completed", ev.subject, ev.subject, sha=ev.data.get("sha", ""))


def _rs_resolved(n, ev):
    """B191: which side of an offline divergence was kept -- a decision, so it replays."""
    d = ev.data
    kept = []
    if d.get("definition"):
        df = d["definition"]
        kept.append(f"kept the definition '{df.get('title', '')}' by {df.get('agent', '?')}")
    if d.get("claim"):
        kept.append(f"kept the claim of {d['claim'].get('lease', {}).get('holder', '?')}")
    text = f"{ev.subject}: contest resolved — {'; '.join(kept) or 'nothing kept'}"
    return ReplayStep(n, ev.ts, "resolved", text, ev.subject)


def _rs_definition(n, ev):
    """A managed definition (`core.defs`) and each revision of it: what a rebuild must
    define again, and what it must not (retired, superseded, merged)."""
    d = ev.data
    kind, rid = d.get("kind", "?"), d.get("id", "?")
    verb = ev.kind.partition(".")[2]
    text = f"{kind} `{rid}` {verb}"
    if d.get("successor"):
        text += f" into `{d['successor']}`" if verb == "merged" else f" by `{d['successor']}`"
    if d.get("reason"):
        text += f": {d['reason']}"
    if d.get("source"):
        text += f" (from {d['source']})"
    if isinstance(d.get("fields"), dict) and d["fields"]:
        text += "\n\n" + json.dumps(d["fields"], indent=2, sort_keys=True, ensure_ascii=False)
    return ReplayStep(n, ev.ts, "definition", text)


#: kind -> renderer. A table rather than a ladder: each arm is independent, and the
#: set of kinds that carry irreplaceable intent is exactly what this dict declares.
_REPLAY_RENDERERS = {
    "session.prompt": _rs_prompt,
    "session.note": _rs_note,
    "session.started": _rs_session_started,
    "session.ended": _rs_session_ended,
    "phase.added": _rs_phase,
    "task.added": _rs_task,
    "decision.recorded": _rs_decision,
    "decision.superseded": _rs_decision_superseded,
    "research.recorded": _rs_research,
    "lesson.recorded": _rs_lesson,
    "item.completed": _rs_completed,
    "item.resolved": _rs_resolved,
    "record.extended": _rs_extended,
    "link.recorded": _rs_link,
    "skew.overridden": _rs_skew_overridden,
    "upgrade.applied": _rs_upgrade_applied,
    **dict.fromkeys(
        ("def.recorded", "def.updated", "def.retired", "def.superseded", "def.merged"),
        _rs_definition,
    ),
}


def render_reconstruction(state: State, steps: list[ReplayStep], *, project: str = "") -> str:
    """A self-contained brief that an agent — any agent — can rebuild the project from.

    Written as instructions to a fresh agent rather than as a report about the past,
    because that is what it is for. The recorded shas are included as *verification*
    anchors with an explicit note that they will not be reproduced, so a reader does
    not mistake a provenance record for a promise of byte-identity.
    """
    out: list[str] = []
    A = out.append
    A(f"# Reconstruction brief{' — ' + project if project else ''}")
    A("")
    A("This document was generated from ddflow's event log by `ddflow replay`. It is")
    A("the complete decision history of the project: every operator instruction, every")
    A("research verdict, every lesson, and the shape of the work queue.")
    A("")
    A("**How to use it.** Hand it to a coding agent with an empty repository and ask it")
    A("to work through the instructions in order. It will not reproduce the original")
    A("source byte-for-byte — model outputs are not deterministic — but it has every")
    A("input that produced the original, which no other artefact does.")
    A("")
    A(f"- Phases: {len(state.phases())}")
    A(f"- Tasks: {len(state.tasks())}")
    A(f"- Lessons carried forward: {len(state.lessons)}")
    A(f"- Architectural decisions in force: {len([d for d in state.decisions.values() if d.live])}")
    A(
        f"- Research notes: {len(state.research)} "
        f"({sum(1 for r in state.research.values() if r.verdict == 'CONFIRMED')} confirmed, "
        f"{sum(1 for r in state.research.values() if r.verdict == 'REFUTED')} refuted)"
    )
    A("")
    _rc_decisions(A, state)
    _rc_knowledge(A, state)
    A("## The instruction history")
    A("")
    for s in steps:
        tag = {
            "prompt": "OPERATOR",
            "note": "note",
            "session": "session",
            "phase": "PHASE",
            "task": "TASK",
            "research": "RESEARCH",
            "lesson": "LESSON",
            "decision": "DECISION",
            "completed": "SHIPPED",
            "definition": "DEFINITION",
        }.get(s.kind, s.kind)
        head = f"### {s.n}. [{tag}] {s.at}"
        if s.item:
            head += f" — `{s.item}`"
        A(head)
        if s.verdict:
            A(f"**Verdict: {s.verdict}**")
        if s.older:
            A(f"_written by an older ddflow ({s.older}) under a skew override_")
        if s.sha:
            A(f"_original commit `{s.sha}` (verification anchor; a rebuild will differ)_")
        A("")
        body = s.text.strip()
        A(textwrap.indent(body, "> " if s.kind == "prompt" else "") if body else "_(empty)_")
        A("")
    return "\n".join(out)


def _rc_decisions(A, state: State) -> None:
    """The architectural decisions section of the reconstruction brief."""
    live = [d for d in state.decisions.values() if d.live]
    if not live:
        return
    A("## Architectural decisions in force")
    A("")
    A("These bind the rebuild. They are the part no other artefact records: source")
    A("code shows what was built and never why, nor what was rejected on the way.")
    A("")
    for d in sorted(live, key=lambda x: x.at):
        A(f"- **{d.title}** — {d.decision}")
        if d.alternatives:
            A(f"  - rejected: {d.alternatives}")
        if d.globs:
            A(f"  - governs: {', '.join(d.globs)}")
    A("")


def _rc_knowledge(A, state: State) -> None:
    """Lessons, then the approaches already tried and rejected."""
    A("## Standing knowledge — read before starting")
    A("")
    if state.lessons:
        A("These were learned the hard way during the original build. They are inputs,")
        A("not history: applying them is how the rebuild avoids repeating the mistakes.")
        A("")
        for ls in sorted(state.lessons.values(), key=lambda x: x.at):
            if ls.superseded_by:
                continue
            A(f"- **{ls.title}** — {ls.rule}")
    else:
        A("_No lessons were recorded._")
    A("")
    refuted = [r for r in state.research.values() if r.verdict == "REFUTED"]
    if not refuted:
        return
    A("### Approaches already tried and rejected")
    A("")
    A("Re-researching these is the single largest waste a rebuild can incur.")
    A("")
    for r in refuted:
        A(
            f"- **{r.claim or r.question}** — REFUTED."
            + (f" Falsifier: {r.falsifier}" if r.falsifier else "")
        )
        if r.probe:
            A(f"  - probe: `{r.probe}`")
        if r.probe_output:
            A("  - measured:")
            A("")
            A("    ```")
            for line in r.probe_output.strip().splitlines():
                A(f"    {line}")
            A("    ```")
    A("")


def verify(state: State, repo: Path, cfg: Config) -> list[str]:
    """Check the log still describes the repository it claims to.

    Every recorded commit sha must resolve. A sha that does not is not necessarily
    corruption — a rebased or squashed branch loses shas legitimately — so the report
    says which and lets a human judge rather than declaring the log broken.
    """
    from ..infra.worktree import git

    problems: list[str] = []
    if not cfg.session.replay_verify_diffs:
        return ["verification disabled ([session].replay_verify_diffs=false)"]
    for it in state.items.values():
        if not it.merged_sha:
            continue
        if not git(repo, "cat-file", "-e", f"{it.merged_sha}^{{commit}}").ok:
            problems.append(
                f"{it.id}: recorded commit {it.merged_sha} no longer resolves "
                f"(rebased, squashed, or a different repository)"
            )
    return problems


def bundle(
    state: State,
    steps: list[ReplayStep],
    out_dir: Path,
    cfg: Config | None = None,
    *,
    project: str = "",
) -> list[Path]:
    """Write a self-contained recovery kit: the brief, the queue, the knowledge.

    Uses `views.markdown.render_views`, the one view map. It used to hold its own copy,
    which had drifted twice: first by a whole file (the research log, where the rejected
    approaches live, was silently omitted), then by bytes -- while this docstring said it
    used the same map. Pinned by
    `tests/test_generated_views.py::test_rendering_is_deterministic_and_the_bundle_writes_the_same_bytes`.
    """
    from ..views.markdown import render_views

    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "RECONSTRUCTION.md"]
    written[0].write_text(render_reconstruction(state, steps, project=project), "utf-8")
    for name, text in render_views(state, cfg).items():
        (out_dir / name).write_text(text, "utf-8")
        written.append(out_dir / name)
    # The rule files are a view of the rule definitions (D-unify 7): rebuilt from the log.
    from .guidance import ruleview as RV

    for rid in RV.live_rule_ids(state):
        target = out_dir / "rules" / f"{rid}.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(RV.render(state, rid), "utf-8")
        written.append(target)
    return written
