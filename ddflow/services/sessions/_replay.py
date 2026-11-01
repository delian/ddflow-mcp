"""Replay: the ordered intent history of a project, folded from the event log.

`replay` filters the log to the kinds that carry irreplaceable information and renders
each through `_REPLAY_RENDERERS` (one `_rs_*` arm per kind) into a `ReplayStep`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...core.events import OLDER_MARK, stamp_text
from ...core.model import ADD_RELATIONS, link_targets
from ...infra.log import PROVENANCE_KINDS, Event


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


def _rs_upgrade_progress(n, ev):
    """A run's progress record (`status` started, step or failed): what happened, not an upgrade."""
    d = ev.data
    run = d.get("run", "?")
    if d.get("status") == "started":
        text = f"Upgrade run {run} started: {len(d.get('items') or [])} item(s)"
    elif d.get("status") == "step":
        text = f"Upgrade run {run}: {d.get('step', '?')} {d.get('step_status', '?')}"
    else:
        text = f"Upgrade run {run} ended without an upgrade: {d.get('summary', '')}"
    return ReplayStep(n, ev.ts, "session", text)


def _rs_upgrade_applied(n, ev):
    d = ev.data
    if d.get("status", "done") != "done":
        return _rs_upgrade_progress(n, ev)
    cats = ", ".join(d.get("categories") or []) or "nothing"
    backup = f"; originals saved in {d['backup']}" if d.get("backup") else ""
    confirmed = ", ".join(sorted(d.get("confirmed") or {}))
    sure = f"; the operator confirmed {confirmed}" if confirmed else ""
    # `stamp_text`, so a partial apply's marker is not named as a release the project
    # reached (B439c42848f). It comes from `core.events`, which a service may import.
    frm, to = stamp_text(d.get("from") or ""), stamp_text(d.get("to") or "")
    return ReplayStep(
        n,
        ev.ts,
        "session",
        f"Upgraded the project from ddflow {frm} to {to}: {cats} "
        f"({len(d.get('items') or [])} item(s)){sure}{backup}",
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
