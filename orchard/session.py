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

from .config import Config
from .events import PROVENANCE_KINDS, Event, EventLog
from .model import State


def redact(text: str, cfg: Config) -> tuple[str, int]:
    """Scrub secrets. Returns (clean_text, n_redactions)."""
    n = 0
    out = text
    for pat in cfg.session.redact_patterns:
        out, k = re.subn(pat, lambda m: _mask(m.group(0)), out)
        n += k
    return out, n


def _mask(s: str) -> str:
    if ":" in s or "=" in s:
        sep = ":" if ":" in s else "="
        head, _, _ = s.partition(sep)
        return f"{head}{sep} [REDACTED]"
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
        d = ev.data
        if ev.kind == "session.prompt":
            steps.append(ReplayStep(n, ev.ts, "prompt", d.get("text", ""), d.get("item", "")))
        elif ev.kind == "session.note":
            steps.append(ReplayStep(n, ev.ts, "note", d.get("text", ""), d.get("item", "")))
        elif ev.kind == "session.started":
            steps.append(
                ReplayStep(
                    n,
                    ev.ts,
                    "session",
                    f"session {ev.subject} opened on {d.get('model') or 'unknown model'}",
                )
            )
        elif ev.kind == "session.ended":
            steps.append(
                ReplayStep(
                    n, ev.ts, "session", f"session {ev.subject} closed. {d.get('summary', '')}"
                )
            )
        elif ev.kind == "phase.added":
            steps.append(
                ReplayStep(n, ev.ts, "phase", f"{ev.subject}: {d.get('title', '')}", ev.subject)
            )
        elif ev.kind == "task.added":
            needs = ", ".join(d.get("needs", [])) or "-"
            steps.append(
                ReplayStep(
                    n,
                    ev.ts,
                    "task",
                    f"{ev.subject}: {d.get('title', '')} (in {d.get('parent', '')}; needs {needs})",
                    ev.subject,
                )
            )
        elif ev.kind == "research.recorded":
            steps.append(
                ReplayStep(
                    n,
                    ev.ts,
                    "research",
                    f"{d.get('question', '')} -> {d.get('claim', '')}",
                    d.get("item", ""),
                    verdict=d.get("verdict", ""),
                )
            )
        elif ev.kind == "lesson.recorded":
            steps.append(
                ReplayStep(n, ev.ts, "lesson", f"{d.get('title', '')}: {d.get('rule', '')}")
            )
        elif ev.kind == "item.completed":
            steps.append(
                ReplayStep(n, ev.ts, "completed", ev.subject, ev.subject, sha=d.get("sha", ""))
            )
    return steps


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
    A(
        f"- Research notes: {len(state.research)} "
        f"({sum(1 for r in state.research.values() if r.verdict == 'CONFIRMED')} confirmed, "
        f"{sum(1 for r in state.research.values() if r.verdict == 'REFUTED')} refuted)"
    )
    A("")
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
    if refuted:
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
                # The measurement, not just the command. This is the line that makes
                # the rejection re-checkable instead of merely assertable.
                A("  - measured:")
                A("")
                A("    ```")
                for line in r.probe_output.strip().splitlines():
                    A(f"    {line}")
                A("    ```")
        A("")
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


def verify(state: State, repo: Path, cfg: Config) -> list[str]:
    """Check the log still describes the repository it claims to.

    Every recorded commit sha must resolve. A sha that does not is not necessarily
    corruption — a rebased or squashed branch loses shas legitimately — so the report
    says which and lets a human judge rather than declaring the log broken.
    """
    from .worktree import git

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
    from .render import board, lessons_md, research_md

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
