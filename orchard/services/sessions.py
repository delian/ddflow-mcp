"""Sessions, prompt provenance, and reconstruction from the log alone.

The requirement this module serves is unusual and worth stating plainly: *if
everything except the log were lost, could the project be rebuilt?* Not byte-for-byte
— an LLM is not a deterministic function, so replaying prompts cannot reproduce the
same source. What CAN be reproduced is the **decision history**: every operator
instruction, in order, with the state it applied to, the research that informed it, the
gates it passed and the commit it produced.

So Orchard records two layers, and keeps them separate on purpose:

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

import os
import re
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core.model import State
from ..infra.log import PROVENANCE_KINDS, Event, EventLog


def redact(text: str, cfg: Config) -> tuple[str, int]:
    """Scrub secrets. Returns (clean_text, n_redactions).

    **An invalid pattern raises rather than being skipped.** This is a security control
    writing to a COMMITTED log, and its failure mode is silence: a pattern that does not
    compile simply stops matching, so secrets flow into git while the config still lists
    the rule that was supposed to stop them. The commonest way to get one is setting the
    list from an environment variable, where comma-splitting tears any regex containing
    a quantifier like `{16,}` into two invalid halves — so the error names that remedy.
    """
    n = 0
    out = text
    for pat in cfg.session.redact_patterns:
        try:
            compiled = re.compile(pat)
        except re.error as exc:
            raise ValueError(
                f"redaction pattern {pat!r} does not compile: {exc}.\n"
                f"If you set [session].redact_patterns from an environment variable, a "
                f"regex containing a comma (e.g. the quantifier '{{16,}}') is split on "
                f"it. Pass the list as JSON instead:\n"
                f"""  ORCHARD_SESSION_REDACT_PATTERNS='["pattern one", "pattern two"]'"""
            ) from exc
        out, k = compiled.subn(lambda m: _mask(m.group(0)), out)
        n += k
    return out, n


#: A `Bearer <token>` match splits into exactly two parts: the scheme and the secret.
_SCHEME_AND_VALUE = 2


def _mask(s: str) -> str:
    """Replace the secret, keeping enough context that the line still reads.

    `api_key: abc` becomes `api_key: [REDACTED]` and `Bearer abc` becomes
    `Bearer [REDACTED]`, so a reader (or a replay) can still see WHAT was supplied
    without the value. A whole-match blanking would make the surrounding prompt
    ungrammatical and harder to follow months later.
    """
    for sep in (":", "="):
        if sep in s:
            head, _, _ = s.partition(sep)
            return f"{head}{sep} [REDACTED]"
    parts = s.split(None, 1)
    if len(parts) == _SCHEME_AND_VALUE:
        return f"{parts[0]} [REDACTED]"
    return "[REDACTED]"


def new_session_id() -> str:
    return time.strftime("s%Y%m%dT%H%M%S", time.gmtime()) + f"-{os.getpid():d}"


def start(
    log: EventLog, cfg: Config, *, model: str = "", agent_tool: str = "", session_id: str = ""
) -> str:
    sid = session_id or new_session_id()
    log.append("session.started", sid, {"model": model, "tool": agent_tool, "cwd": str(Path.cwd())})
    return sid


def prompt(log: EventLog, cfg: Config, session_id: str, text: str, *, item: str = "") -> int:
    """Record one operator prompt verbatim (after redaction). Returns redaction count."""
    if not cfg.session.log_prompts:
        return 0
    clean, n = redact(text, cfg)
    log.append("session.prompt", session_id, {"text": clean, "item": item, "redactions": n})
    return n


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


def replay(events: list[Event], *, include_outcomes: bool = True) -> list[ReplayStep]:
    """The ordered intent history — the input to a from-scratch rebuild.

    Filters the log to the kinds that carry irreplaceable information. Gate evidence
    and lease churn are deliberately excluded: they describe *how the work was checked*,
    not *what was asked for*, and including them buries the twenty sentences that
    matter under ten thousand that do not.
    """
    steps: list[ReplayStep] = []
    n = 0
    for ev in events:
        if ev.kind not in PROVENANCE_KINDS and not (
            include_outcomes and ev.kind == "item.completed"
        ):
            continue
        n += 1
        step = _REPLAY_RENDERERS.get(ev.kind, lambda _n, _e: None)(n, ev)
        if step is not None:
            steps.append(step)
    return steps


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
    text = f"{ev.subject}: {d.get('title', '')}"
    if d.get("body"):
        text += "\n\n" + d["body"].strip()
    return ReplayStep(n, ev.ts, "phase", text, ev.subject)


def _rs_task(n, ev):
    d = ev.data
    needs = ", ".join(d.get("needs", [])) or "-"
    text = (
        f"{ev.subject}: {d.get('title', '')} "
        f"(in {d.get('parent', '')}; needs {needs}"
        + (f"; writes {', '.join(d['globs'])}" if d.get("globs") else "")
        + ")"
    )
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
    return ReplayStep(n, ev.ts, "decision", "\n\n".join(parts), d.get("item", ""))


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
        f"{d.get('question', '')} -> {d.get('claim', '')}",
        d.get("item", ""),
        verdict=d.get("verdict", ""),
    )


def _rs_lesson(n, ev):
    d = ev.data
    return ReplayStep(n, ev.ts, "lesson", f"{d.get('title', '')}: {d.get('rule', '')}")


def _rs_completed(n, ev):
    return ReplayStep(n, ev.ts, "completed", ev.subject, ev.subject, sha=ev.data.get("sha", ""))


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
    A("This document was generated from Orchard's event log by `orchard replay`. It is")
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
        }.get(s.kind, s.kind)
        head = f"### {s.n}. [{tag}] {s.at}"
        if s.item:
            head += f" — `{s.item}`"
        A(head)
        if s.verdict:
            A(f"**Verdict: {s.verdict}**")
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

    Uses the same generator map as `render.write_views`. The two used to be separate
    copies and had already drifted by one file — the bundle silently omitted the
    research log, which is where the rejected approaches live.
    """
    from ..views.markdown import board, lessons_md, research_md

    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "RECONSTRUCTION.md"]
    written[0].write_text(render_reconstruction(state, steps, project=project), "utf-8")
    for name, text in (
        ("QUEUE.md", board(state, cfg)),
        ("LESSONS.md", lessons_md(state)),
        ("RESEARCH.md", research_md(state)),
    ):
        (out_dir / name).write_text(text, "utf-8")
        written.append(out_dir / name)
    return written
