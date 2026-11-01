"""Reconstruction: the brief a fresh agent rebuilds the project from, its verification and
the recovery kit.

`render_reconstruction` turns replay steps into the brief, `verify` checks each recorded
commit sha still resolves, and `bundle` writes the brief, the queue, the knowledge and the
rule files to a directory.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from ...config import Config
from ...core.model import State
from ...infra.fsio import replace_text
from ...infra.worktree import git
from ...views.markdown import render_views
from ..guidance import ruleview as RV
from ._replay import ReplayStep


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
            if not ls.live:
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
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "RECONSTRUCTION.md"]
    replace_text(written[0], render_reconstruction(state, steps, project=project))
    for name, text in render_views(state, cfg).items():
        replace_text(out_dir / name, text)
        written.append(out_dir / name)
    # The rule files are a view of the rule definitions (D-unify 7): rebuilt from the log.
    for rid in RV.live_rule_ids(state):
        target = out_dir / "rules" / f"{rid}.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_text(target, RV.render(state, rid))
        written.append(target)
    return written
