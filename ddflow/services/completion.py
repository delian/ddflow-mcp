"""Whether an item may be completed — the rule-set, callable without argv.

These rules were the body of `cli.cmd_complete`: required gates, open descendants,
silence under `require_outcome`, inert requirements, unavailable gates, reviewer
independence. Real domain policy, reachable only through `main(argv)` — so the only way
to ask "would this complete, and why not" was to run the CLI and read its stderr, and
the only way to test a rule was to drive a subprocess.

Policy belongs where it can be called. `verdict()` answers the question and writes
nothing; the surfaces decide what to print, which exit code to use, and whether
`--force` overrides. That split is also what lets `--force` be honest: the blockers are
computed either way and recorded on the completion event, so a forced completion says
in the log exactly what it overrode.

The one thing deliberately NOT a blocker is stale evidence. A gate that passed on a
different tree still passed; what is no longer true is that it passed on *this* tree.
Refusing on a change that might be a comment is how a check gets switched off, so it
comes back as a `warning` and the surfaces show it before the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.model import State
from . import gates as G


@dataclass
class Verdict:
    """Everything the decision depends on, computed once.

    `blockers` is the complete list, never the first one found: reporting one at a time
    turns a single refusal into a round-trip per problem, and an agent that cannot see
    how many more are coming reaches for `--force`. A refusal is only actionable if it
    is complete.
    """

    item: str
    blockers: list[str] = field(default_factory=list)
    #: Pre-decision WARNINGS, addressed to whoever is about to complete: a gate whose
    #: evidence describes a tree that has since moved. Shown before the verdict, and on
    #: stderr, because they are about the decision rather than part of its result.
    warnings: list[str] = field(default_factory=list)
    #: Part of the RESULT, not a warning about it: the coverage gap, which is recorded
    #: on the completion event and must reach both surfaces. Kept separate from
    #: `warnings` because merging them put it on stderr, where the one surface that
    #: needed it -- an agent reading JSON -- could not see it. That is the exact bug
    #: this field's comment in `cmd_complete` was written about.
    coverage_note: str = ""
    #: Gates in the pipeline that could not run. Recorded on the event as a coverage
    #: gap, so a completion never silently implies they passed.
    coverage_gaps: list[str] = field(default_factory=list)
    independence: str = ""
    kind: str = "task"

    @property
    def may_complete(self) -> bool:
        return not self.blockers


def verdict(state: State, cfg: Config, item_id: str, *, repo: Path, model: str = "") -> Verdict:
    """Why this item may or may not complete. Reads only; writes nothing."""
    it = state.items.get(item_id)
    if it is None or it.removed:
        return Verdict(item=item_id, blockers=[f"no such item {item_id!r}"])

    s = G.status(state, cfg, item_id)
    v = Verdict(item=item_id, coverage_gaps=list(s.unavailable), kind=it.kind)
    required = set(cfg.gates.required)

    missing = [g for g in s.pipeline if g in required and it.gate_outcome(g) != "passed"]
    if missing:
        v.blockers.append(
            f"required gate(s) not passed: {', '.join(missing)} (current outcome: "
            + ", ".join(f"{g}={it.gate_outcome(g) or 'not run'}" for g in missing)
            + ")"
        )

    # ANY item with work beneath it, not only a phase: a task split into sub-tasks is
    # an umbrella too, and completing it while its children are open marks work
    # finished that nobody has done. `abandoned` counts as settled alongside `done`, or
    # an item you decided against holds its parent open forever.
    open_children = state.open_descendants(item_id)
    if open_children:
        noun = "task(s) in this phase" if it.kind == "phase" else "sub-task(s)"
        v.blockers.append(
            f"{len(open_children)} {noun} unfinished: {', '.join(k.id for k in open_children[:8])}"
        )

    # Silence is not a pass. Without this an agent could complete having recorded
    # implement/unit_tests/merge and never touched research, the rubber-duck, the
    # critic, the standards pass, the bug hunt or the dedupe check — six of ten steps
    # omitted with no trace. An explicit `gate skip --reason` is still an outcome, so
    # the escape hatch is the auditable one rather than the invisible one.
    if cfg.gates.require_outcome and s.silent:
        gdefs = G.load_gates(repo, cfg)
        human = [g for g in s.silent if g in gdefs and gdefs[g].is_human_gate]
        other = [g for g in s.silent if g not in human]
        if other:
            v.blockers.append(
                f"gate(s) never run and never skipped: {', '.join(other)}. "
                f"Record an outcome (`ddflow gate run|record`) or skip it on the record "
                f"(`ddflow gate skip <id> <gate> --reason ...`); "
                f"set [gates].require_outcome = false to make the pipeline advisory."
            )
        if human:
            # Named separately, because all three commands the sentence above suggests
            # REFUSE a human gate. Telling an agent to run them is telling it to
            # collect an exit 3 and conclude something is broken.
            v.blockers.append(
                f"awaiting a person: {', '.join(human)}. Ask the operator to run "
                f"`ddflow approve {item_id} {human[0]}` (or `--reject --reason ...`). "
                f"You cannot record this one — `gate record` and `gate skip` both "
                f"refuse it, and there is no MCP tool for it."
            )

    inert = G.inert_requirements(cfg)
    if inert:
        v.blockers.append(
            f"[gates].required names {', '.join(inert)}, which no pipeline runs — "
            f"so that requirement enforces nothing. Add it to task_pipeline or "
            f"phase_pipeline, or drop it from required."
        )

    if cfg.gates.unavailable_is_failure and s.unavailable:
        v.blockers.append(
            f"gate(s) could not run: {', '.join(s.unavailable)} "
            f"([gates].unavailable_is_failure is on, so a gap blocks like a failure)"
        )

    ok, why = G.reviewer_independence(state, cfg, item_id, model)
    v.independence = why
    if cfg.agent.reviewer_family_must_differ and not ok and it.kind == "task":
        v.blockers.append(f"reviewer independence not satisfied: {why}")

    if s.unavailable:
        v.coverage_note = (
            f"{', '.join(s.unavailable)} never ran — recorded as a coverage gap, not as a pass."
        )

    from ..infra import worktree as W

    wt = (W.load_path(repo, it.worktree) if it.worktree else None) or repo
    for gid in G.stale_evidence(state, cfg, item_id, wt):
        v.warnings.append(
            f"{gid} passed on a different tree than the one you are completing — the "
            f"working tree changed after it ran. Re-run it if the change was not "
            f"cosmetic."
        )
    return v
