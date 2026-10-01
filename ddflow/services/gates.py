"""Gates — the quality pipeline every task and every phase passes through.

A gate is one checkpoint with one outcome. Two kinds exist and the difference matters:

* a **command gate** has a shell command; ddflow runs it and the exit code decides;
* an **agent gate** has none, because the work is judgement an LLM does (research,
  a rubber-duck review, a bug hunt). ddflow cannot perform it, so it *demands the
  evidence* and records the agent's answer.

Agent gates are where a workflow usually rots, because "I reviewed it" costs nothing
to say. Three rules push back:

1. **UNAVAILABLE is an outcome, not a synonym for pass.** A reviewer whose endpoint was
   down did not approve anything. It gets its own outcome, its own exit code, and it
   shows up in the completion report as a gap.
2. **Evidence is required for the gates listed in ``gates.evidence_required``.** A
   record with no command, no exit code and no output digest is rejected at the API
   boundary, so a gate cannot be passed by assertion.
3. **A different-family reviewer is checked, not trusted.** Same-family reviewers share
   the author's blind spots, so their agreement is not independent evidence. The runner
   knows which family produced each review and reports when they all match the author.

Exit-code vocabulary, uniform across every ddflow command:
``0`` healthy · ``1`` real failure · ``2`` could not run / nothing to do · ``3``
coordination refused. ``2`` is never collapsed into ``0``; "no data" is reported, never
treated as "no problem".
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.model import GATE_OUTCOMES, Item, State
from ..infra import proc as P
from ..infra.log import EventLog

# Exit vocabulary lives in ONE place: `cli.py`. It used to be declared here too, with a
# different third name for the same code, and nothing imported this copy.


@dataclass
class GateDef:
    id: str
    title: str = ""
    description: str = ""
    command: str = ""
    required: bool = False
    evidence: bool = False
    timeout_s: int = 1800
    reviewer: str = ""  # "" | "different_family" | "same_family_ok"
    applies_to: str = "task"  # task | phase | both
    cwd: str = "worktree"  # worktree | repo
    env: dict[str, str] = field(default_factory=dict)
    prompt: str = ""  # instruction handed to the agent for an agent gate
    #: Edits that MUST make this gate fail. Each `{file, old, new}` is applied to the
    #: worktree, the gate is run, and a non-zero exit is required before the file is
    #: restored. A gate with no registered mutation is a gate nobody has shown can go
    #: red — see `verify`.
    mutations: list[dict[str, str]] = field(default_factory=list)
    #: Exit codes that mean "could not run" / "ran part of it", for tools that SAY so by
    #: exit code. A cross-family critic that exits 2 when its endpoint is down and 3 when
    #: only some files were reviewed was recorded as FAILED either way -- and a failure
    #: that is really an outage sends the author off to fix code that nobody reviewed.
    unavailable_exits: list[int] = field(default_factory=list)
    partial_exits: list[int] = field(default_factory=list)
    #: For tools whose exit code does not carry the verdict. `require_output`: a regex
    #: that must appear for exit 0 to count as PASSED -- absent, the tool did not
    #: demonstrably do its job, which is UNAVAILABLE (a reviewer that exits 0 having
    #: degenerated into nothing). `fail_output`: a regex whose presence FAILS an exit 0
    #: (a reviewer that reports findings and exits 0 anyway).
    require_output: str = ""
    fail_output: str = ""

    #: This gate is satisfied by a PERSON, not by the agent and not by a command.
    #: See :meth:`is_human_gate`.
    human: bool = False

    @property
    def is_command_gate(self) -> bool:
        return bool(self.command.strip()) and not self.human

    @property
    def is_human_gate(self) -> bool:
        """A checkpoint only the operator can clear.

        Every other gate here is cleared by the agent — it runs a command, or it asserts
        it did the thinking. That is right for work whose correctness is checkable after
        the fact, and wrong for a plan: by the time an agent has implemented the wrong
        thing, the cost is already paid. A human gate is where the operator says "yes,
        build that" BEFORE the compute is spent.

        **What this is, precisely.** An audit trail and a speed bump, NOT a security
        boundary. An agent with shell access can run `ddflow approve` itself, and no
        amount of design here changes that — the tool does not control the machine.

        What it guarantees, stated as narrowly as it holds: **no MCP tool records a
        human outcome.** `gate record` and `gate skip` refuse, and there is no approve
        tool. An earlier version of this paragraph claimed satisfying the gate was
        "impossible through the MCP surface at all", and that was FALSE: two calls —
        `ddflow_configure` setting `gate.<id>.human = false`, then `ddflow_gate_record`
        — cleared it with no shell involved. That path is now refused by `_write_config`
        (the flag is not an editable preference), but the honest claim is the narrow one,
        because the broad one was the kind of overclaim this project keeps catching in
        its own docstrings and this docstring was no exception.

        And (b): a clearance is recorded with the OS user and a `human` flag, so a
        forged one is visible in the log rather than indistinguishable from a real one.
        """
        return self.human


#: The shipped default pipeline. It is the operator's ten steps, in their order, with
#: the phase-level pass expressed in the same vocabulary. Every field is overridable
#: from `.ddflow/gates.toml`; nothing here is compiled in.
DEFAULT_GATES: dict[str, GateDef] = {
    "research": GateDef(
        id="research",
        title="Research",
        applies_to="both",
        evidence=True,
        description="State a falsifiable claim before writing code, and probe it.",
        prompt=(
            "Before implementing: state the claim, its mechanism, the single "
            "observation that would REFUTE it, and the cheapest test that could. "
            "Run that test and paste its command AND output. Record with "
            "`ddflow research add --verdict CONFIRMED|REFUTED|THEORETICAL`. "
            "A pass with no CONFIRMED/REFUTED label is a literature summary, "
            "not research."
        ),
    ),
    "rules": GateDef(
        id="rules",
        title="Rules, lessons and memory",
        applies_to="both",
        description="Load project rules and the lessons that bear on THIS task.",
        prompt=(
            "Run `ddflow brief --item <id>`. It returns the project rules digest "
            "and the handful of past lessons ranked against this task's text. Do "
            "not read the whole lessons corpus; that is what the ranking is for."
        ),
    ),
    "implement": GateDef(
        id="implement",
        title="Implement",
        required=True,
        applies_to="task",
        description="Write the change in the task's own worktree.",
        prompt=(
            "Implement in the worktree ddflow created. Touch only files inside "
            "this task's declared globs; if you must widen them, run "
            "`ddflow update <id> --globs <every glob, old and new>` FIRST (it "
            "replaces the list, and moves your lease to it) so the conflict "
            "detector can see it."
        ),
    ),
    "rubber_duck": GateDef(
        id="rubber_duck",
        title="Rubber-duck review",
        evidence=True,
        reviewer="different_family",
        applies_to="task",
        description="An independent model tries to REFUTE the change.",
        prompt=(
            "Dispatch a reviewer on a DIFFERENT model family than the author. Ask "
            "it to refute, not to review: 'find the input that makes this wrong'. "
            "When uncertain it must report nothing — a reviewer rewarded for "
            "finding things finds things."
        ),
    ),
    "critic": GateDef(
        id="critic",
        title="Cross-family critic",
        evidence=True,
        reviewer="different_family",
        applies_to="task",
        description="A second, differently-trained critic on the diff and the intent.",
        prompt=(
            "Run the configured critic over the diff WITH the task's intent. It "
            "flags where the diff and the stated intent disagree. Exit 2 means "
            "UNAVAILABLE and must be recorded as such — never as a pass."
        ),
    ),
    "standards": GateDef(
        id="standards",
        title="Coding standards",
        evidence=True,
        applies_to="task",
        description="Automated standards/architecture review (roborev, codeguide, linters).",
        prompt=(
            "Run the project's standards tools. Apply their FINDINGS; verify their "
            "FIXES by running the tests — a suggested fix reasoning from general "
            "language rules does not know your types' operator overloads."
        ),
    ),
    "unit_tests": GateDef(
        id="unit_tests",
        title="Unit tests",
        required=True,
        evidence=True,
        applies_to="both",
        command="",
        timeout_s=3600,
        description="The project's WHOLE test suite must pass, run in parallel.",
        prompt=(
            "The WHOLE suite, never a selection: a targeted run hides standing breakage. "
            "Run it in parallel -- pytest with `-n auto` (pytest-xdist) or a fixed worker "
            "count; `ddflow workflow` says when the command uses ONE core. While you work, "
            "`ddflow tests --item <ID>` lists the tests your change reaches and a parallel "
            "command for them: fast feedback, not this gate. Set `[gate.unit_tests].command` "
            "in .ddflow/config.toml so this gate runs itself, e.g. `pytest -q -n auto`."
        ),
    ),
    "bug_hunt": GateDef(
        id="bug_hunt",
        title="Bug hunt",
        applies_to="both",
        evidence=True,
        description="Hunt the recurring bug classes across everything this unit touched.",
        prompt=(
            "Hunt: off-by-one, empty-collection/vacuous-truth, silently dropped "
            "config, torn-tail resume, unavailable-treated-as-clean, dead knobs. "
            "A finding may only change source if a runnable probe demonstrates it "
            "AND ships as the regression test in the same commit — then MUTATE the "
            "fix and watch the probe fail. A probe that passes both ways proves "
            "nothing. No probe -> file it as THEORETICAL, change nothing."
        ),
    ),
    "dedupe": GateDef(
        id="dedupe",
        title="Deduplication",
        applies_to="both",
        evidence=True,
        description="Did this work re-implement something the project already has?",
        prompt=(
            "Search by OPERATION, not by the name you invented ('retry with "
            "backoff', 'atomic write'). Start from a mechanical clone report if "
            "the project has one. Second occurrence: reuse or extract. Third: "
            "extraction is mandatory."
        ),
    ),
    "live_test": GateDef(
        id="live_test",
        title="Live smoke run",
        applies_to="phase",
        evidence=True,
        description="Actually run the feature end to end, not just its tests.",
        prompt=(
            "Run the real thing on a small input and paste the output. A green "
            "unit suite and a working feature are different claims."
        ),
    ),
    "docs": GateDef(
        id="docs",
        title="Documentation",
        applies_to="phase",
        evidence=True,
        description="The README and docs describe what this phase changed, before it merges.",
        prompt=(
            "Read the phase's whole diff (the phase base..HEAD) and list every change a "
            "user or an agent can see: commands and MCP tools, flags, config knobs and "
            "their defaults, output, install and setup steps. Check each against the "
            "README and the project's documentation, and update what is missing or wrong "
            "in this phase -- a stale page is worse than a missing one, because a reader "
            "trusts it. Check counts and examples the README states (knob counts, command "
            "samples) still hold. Record the files you changed, or 'no user-visible "
            "change' with the reason; an unexplained pass is not evidence."
        ),
    ),
    "corrections": GateDef(
        id="corrections",
        title="Corrections",
        applies_to="phase",
        description="Apply what the phase-level passes surfaced.",
        prompt="Fix what the phase gates found; each fix carries its own regression test.",
    ),
    "tasks": GateDef(
        id="tasks",
        title="Member tasks",
        required=True,
        applies_to="phase",
        description="Fan-out point: every task in the phase reaches done.",
        prompt=(
            "Not performed by hand. `ddflow next --phase <id>` offers the ready "
            "set; independent tasks run in parallel worktrees."
        ),
    ),
    "merge": GateDef(
        id="merge",
        title="Merge",
        required=True,
        applies_to="both",
        description="Land the work on the base branch.",
        prompt="`ddflow merge <id>` — merges from the primary checkout without a checkout.",
    ),
}


def load_gates(root: Path, cfg: Config) -> dict[str, GateDef]:
    """Defaults, overlaid by ``[gate.*]`` from the config.

    Read from ``.ddflow/config.toml`` first, then ``.ddflow/gates.toml`` if it
    exists. **Both**, because a project should have ONE place to configure and the
    obvious place is the config file — but an operator who prefers to split the gate
    definitions out should not be told they cannot. `gates.toml` wins on a conflict,
    being the more specific file. Then the machine-local layer, git-ignored:
    ``.ddflow/local/config.toml`` and ``.ddflow/local/gates.toml``, which win over both
    -- this machine's worker count, never a project-wide declaration such as a human
    gate, which belongs in the committed ``gates.toml`` (`tomlcfg.config_paths`).

    This read used to look at `gates.toml` ALONE, which meant `ddflow configure` (and
    the `ddflow_configure` MCP tool) accepted a `[gate.unit_tests]` block, wrote it to
    `config.toml`, reported success, and changed nothing. A config write that silently
    does nothing is worse than one that errors.

    Overlay rather than replace: a project that only wants to set
    ``unit_tests.command`` writes three lines and still inherits every prompt and
    policy. A file that had to restate all thirteen gates to change one would be copied
    once and then drift.
    """
    from ..infra import tomlcfg

    gates = {k: GateDef(**{**v.__dict__}) for k, v in DEFAULT_GATES.items()}
    for gid, spec in tomlcfg.overlay_table(
        tomlcfg.config_paths(root, "gates.toml"), "gate", GateDef
    ).items():
        base = gates.get(gid) or GateDef(id=gid)
        for k, v in spec.items():
            setattr(base, k, v)
        base.id = gid
        gates[gid] = base
    # A human checkpoint the COMMITTED files declare cannot be switched off by the
    # git-ignored local layer: a `human = false` there never appears in a diff or a
    # review, so it would quietly hand the operator's gate to any agent on this machine.
    committed = tomlcfg.config_paths(root, "gates.toml")[:2]
    for gid, spec in tomlcfg.overlay_table(committed, "gate", GateDef).items():
        if spec.get("human") and gid in gates:
            gates[gid].human = True
    for gid in cfg.gates.required:
        if gid in gates:
            gates[gid].required = True
    for gid in cfg.gates.evidence_required:
        if gid in gates:
            gates[gid].evidence = True
    return gates


def pipeline_for(item: Item, cfg: Config) -> list[str]:
    if item.promote_to:
        return list(cfg.gates.promotion_pipeline)
    return list(cfg.gates.phase_pipeline if item.kind == "phase" else cfg.gates.task_pipeline)


@dataclass
class GateStatus:
    item: str
    pipeline: list[str]
    done: list[str]
    current: str
    blocked_by: list[str]
    unavailable: list[str]
    skipped: list[str]
    complete: bool
    #: Pipeline gates with NO recorded outcome at all. Silence is its own state: it is
    #: neither a pass nor a failure, and `gates.require_outcome` decides whether it
    #: blocks completion.
    silent: list[str] = field(default_factory=list)

    def render(self) -> str:
        marks = {
            "passed": "[x]",
            "failed": "[!]",
            "unavailable": "[?]",
            "partial": "[~]",
            "skipped": "[-]",
            "": "[ ]",
        }
        return "\n".join(f"  {marks.get(o, '[ ]')} {g}" for g, o in self.rows)

    rows: list[tuple[str, str]] = field(default_factory=list)


def status(state: State, cfg: Config, item_id: str) -> GateStatus:
    """Where is this item in its pipeline, and what is the next thing to do?"""
    it = state.items.get(item_id)
    if it is None:
        raise KeyError(item_id)
    gates = pipeline_for(it, cfg)
    rows = [(g, it.gate_outcome(g)) for g in gates]
    done = [g for g, o in rows if o in ("passed", "skipped")]
    blocked = [g for g, o in rows if o == "failed"]
    unavail = [g for g, o in rows if o in ("unavailable", "partial")]
    skipped = [g for g, o in rows if o == "skipped"]
    current = ""
    for g, o in rows:
        if o not in ("passed", "skipped"):
            current = g
            break
    required = set(cfg.gates.required)
    complete = (
        all(o == "passed" or (o == "skipped" and g not in required) for g, o in rows)
        and not blocked
    )
    return GateStatus(
        item=item_id,
        pipeline=gates,
        done=done,
        current=current,
        blocked_by=blocked,
        unavailable=unavail,
        skipped=skipped,
        complete=complete,
        rows=rows,
        silent=[g for g, o in rows if not o],
    )


def stale_evidence(
    state: State, cfg: Config, item_id: str, cwd: Path, *, landed: str = ""
) -> list[str]:
    """Gates whose evidence is KNOWN to describe a tree that has since changed.

    Stale only: a gate whose evidence could not be compared at all is NOT in this list,
    and an empty list therefore does not mean "all evidence is fresh". A caller that
    needs that distinction -- `complete` does -- reads `stale_evidence_detail`, where
    such a gate is a note with ``unverified`` set.
    """
    notes = stale_evidence_detail(state, cfg, item_id, cwd, landed=landed)
    return [n.gate for n in notes if not n.unverified]


@dataclass(frozen=True, order=True)
class StaleNote:
    """A passed gate whose evidence is not about the tree being completed -- or, with
    ``unverified``, one whose evidence could not be compared with it at all. The two
    are kept apart: "could not tell" rendered as "fresh" is a silent pass, and rendered
    as "stale" is a false alarm."""

    gate: str
    why: str
    unverified: bool = False


def stale_evidence_detail(
    state: State, cfg: Config, item_id: str, cwd: Path, *, landed: str = ""
) -> list[StaleNote]:
    """A note for each gate whose evidence describes another tree, or cannot be checked.

    The hazard B21 names, and the ordinary way it happens: run the tests, edit one more
    thing, complete. The recorded pass is then true about source nobody is shipping —
    and it is indistinguishable, in the log, from a pass about the code that shipped.

    Compared against ``cwd``'s working tree, or -- with ``landed``, a commit -- against
    what that commit holds. After `merge` the item's worktree is gone, and the tree
    being completed is the branch that landed, not whatever the primary checkout has
    checked out: comparing against the primary made every gate stale on the ordinary
    path (bugs Bd86b05a8f8, Ba84119f707, B613cb67194), and a warning that always fires
    is one nobody reads.

    CONTENT is compared (`source_tree`), not commit ids: a gate run on uncommitted
    edits, or at the branch tip, is fresh evidence about the merge commit that records
    the same files. Evidence from before `source_tree` was recorded falls back to the
    fingerprint against a working tree, and against a commit only when it was taken on
    a clean tree (its commit then names the content).

    Only gates in `gates.evidence_required` are checked. The others legitimately record
    before the work is finished: `implement` is *supposed* to precede the edits that
    follow it, and flagging that would make this noise, which is how a real warning
    stops being read.

    Evidence that names content which cannot be compared now -- the landed commit or
    the worktree unreadable, or legacy evidence taken on uncommitted edits once the
    worktree is gone -- comes back ``unverified``, not silently fresh. Evidence with no
    tree at all (a gate recorded where nothing could be measured) is not reported.
    """
    it = state.items.get(item_id)
    if it is None:
        return []
    label = f"what landed ({landed[:12]})" if landed else "the working tree"
    entries_cache: dict[str, TreeEntries | None] = {}

    def entries_of(rev: str) -> TreeEntries | None:
        if rev not in entries_cache:
            entries_cache[rev] = (
                worktree_entries(cwd) if rev == "" else commit_tree_entries(cwd, rev)
            )
        return entries_cache[rev]

    now = entries_of(landed)
    now_id = content_id(now)
    fingerprint = None if landed else tree_fingerprint(cwd)
    stale = []
    for gid in cfg.gates.evidence_required:
        rec = it.gates.get(gid)
        if not rec or rec.outcome != "passed":
            continue
        ev = rec.evidence or {}
        was_sha = normal_fingerprint(ev.get("tree_sha", "") or "")
        base, _, dirt = was_sha.partition("+")
        was_id = ev.get("source_tree", "") or ""
        if was_id:
            if not now_id:
                stale.append(StaleNote(gid, f"{label} could not be read", unverified=True))
                continue
            if was_id == now_id:
                continue
        elif dirt == "clean" and base:
            then = commit_tree_entries(cwd, base)
            if then is None or now is None:
                why = f"neither {base} nor {label} could be read as a tree"
                stale.append(StaleNote(gid, why, unverified=True))
                continue
            if not differing_paths(then, now):
                continue
        elif not landed and was_sha and fingerprint:
            if was_sha == fingerprint:
                continue  # legacy evidence on a dirty tree: only the fingerprint can tell
        elif landed and dirt:
            why = (
                f"it ran on uncommitted edits over {base}, recorded before ddflow kept "
                f"their content, so they cannot be compared with {label}"
            )
            stale.append(StaleNote(gid, why, unverified=True))
            continue
        else:
            continue
        stale.append(StaleNote(gid, _what_differs(cwd, base, dirt, now, label)))
    return sorted(stale)


def _what_differs(cwd: Path, base: str, dirt: str, now: TreeEntries | None, label: str) -> str:
    """Name the files, when the tree the gate ran on can be rebuilt (a clean commit)."""
    then = commit_tree_entries(cwd, base) if base else None
    paths = differing_paths(then, now) if then is not None and now is not None else []
    shown = ", ".join(paths[:8]) + (f" (+{len(paths) - 8} more)" if len(paths) > 8 else "")  # noqa: PLR2004
    if dirt == "clean" and paths:
        return f"it ran on {base}; {label} differs in {shown}"
    if paths:
        return (
            f"it ran on uncommitted edits over {base}, and {label} holds other content "
            f"(files changed since {base}: {shown})"
        )
    return f"the content it ran on is not the content of {label}"


def inert_requirements(cfg: Config) -> list[str]:
    """Gates named in ``gates.required`` that no pipeline actually runs.

    A required gate is enforced by intersecting it with the item's pipeline, which
    means naming one that is in neither pipeline makes the requirement quietly
    **disappear** rather than raise — the vacuous-truth class applied to the very
    mechanism that exists to stop vacuous passes. An operator who sets
    ``required = ["critic"]`` and trims `critic` out of `task_pipeline` gets a project
    where nothing is required at all, reported as fully compliant.

    Reported rather than raised at load time, because a config that is wrong in one
    field should still let `ddflow doctor` run and explain itself.
    """
    pipelines = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
    return sorted(set(cfg.gates.required) - pipelines)


@dataclass
class MutationResult:
    gate: str
    file: str
    applied: bool
    detected: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.applied and self.detected


def verify(
    state: State,
    cfg: Config,
    gates: dict[str, GateDef],
    gate_id: str,
    repo: Path,
    item: Item | None = None,
) -> tuple[list[MutationResult], str]:
    """Break what a gate guards and require it to notice. Returns (results, reason).

    **The anti-vacuous-pass check, turned on ddflow's own checks.** The pipeline has
    ten gates and nothing anywhere proved a single one of them was capable of going
    red. A gate that cannot fail is worse than no gate: it reports success on every
    change, and everyone downstream reads that as evidence.

    Two rules make this honest, and the first is the one that is usually got wrong:

    1. **A mutation that did not apply is not a passed mutation test.** If `old` is not
       found — or is found more than once, so the edit is ambiguous — the check FAILS
       rather than skipping. Silently skipping turns "the mutation never happened" into
       a green run, which reads as "the gate cannot detect this": the exact opposite of
       the truth, delivered confidently.
    2. **The file is restored whatever happens**, including on exception, or a failed
       verification leaves the worktree broken and the next gate reports a failure that
       is the verifier's fault.

    3. **A green baseline is required first.** A gate that is already red — one
       pre-existing failing test, a tool that stopped being installed, a flake — reports
       `failed` for every mutation, so every `detected` comes back True and this
       function certifies it as able to fail. It cannot: it was red before anything was
       touched. That is the vacuous-pass class, inside the check written to catch the
       vacuous-pass class, and it was CONFIRMED by the cross-family critic on
       2026-09-24 with the probe now in `tests/test_gate_verify.py`.

    A gate with zero registered mutations is itself reported as a failure. Declaring a
    check you have never shown can fail is the thing this exists to catch.

    **Callers must check BOTH components.** A non-empty `reason` is a pre-flight
    failure and `results` is then empty — and `all([])` is True, so scoring on
    `results` alone turns every pre-flight failure into a pass. The rule is
    `verified = bool(results) and not reason and all(r.ok for r in results)`.
    """
    gdef = gates.get(gate_id)
    if not gdef:
        return [], f"unknown gate {gate_id!r}; known: {', '.join(sorted(gates))}"
    if gdef.is_human_gate:
        # Named for what it IS. Calling it an agent gate tells the reader its honesty
        # rests on the evidence contract, which is the wrong advice: a human gate rests
        # on a person having looked, and there is no mutation that could demonstrate
        # that.
        return [], (
            f"{gate_id} is a HUMAN-APPROVAL gate — there is no command to mutate, and "
            f"no test could show that a person's judgement can go the other way. It is "
            f"cleared by `ddflow approve` and nothing else."
        )
    if not gdef.is_command_gate:
        return [], (
            f"{gate_id} is an agent gate — it has no command to run, so there is "
            f"nothing to mutate. Its honesty rests on the evidence contract instead."
        )
    if not gdef.mutations:
        return [], (
            f"{gate_id} has NO registered mutations, so nothing has ever shown it can "
            f"fail. Add `mutations = [{{ file = '...', old = '...', new = '...' }}]` to "
            f"[gate.{gate_id}]: an edit that this gate must catch."
        )

    cwd = repo
    if item is not None and gdef.cwd == "worktree" and item.worktree:
        from ..infra import worktree as W

        # No `or repo` fallback. An item that HAS a worktree whose path no longer
        # resolves is a broken state, and falling back writes the mutation into the
        # primary checkout and then reports a verdict about the wrong tree. Refused,
        # named, and nothing is touched.
        resolved = W.load_path(repo, item.worktree)
        if resolved is None:
            return [], (
                f"{item.id} records a worktree ({item.worktree}) that no longer "
                f"resolves. Refusing to mutate the primary checkout in its place — "
                f"run `ddflow recover` or re-claim the item first."
            )
        cwd = resolved

    # The baseline. Everything below compares against it, and without it a red gate
    # "detects" every mutation.
    baseline, base_ev = run_command_gate(gdef, cwd)
    if baseline != "passed":
        return [], (
            f"{gate_id} does not pass on the UNMUTATED source — it reported "
            f"{baseline!r} before anything was changed, so there is no green baseline "
            f"to compare against and a 'detected' result would prove nothing. "
            f"{base_ev.get('reason', '')}".strip()[:400]
        )

    results: list[MutationResult] = []
    for mut in gdef.mutations:
        target = cwd / mut.get("file", "")
        old, new = mut.get("old", ""), mut.get("new", "")
        if not target.is_file():
            results.append(
                MutationResult(gate_id, mut.get("file", ""), False, False, "file not found")
            )
            continue
        src = target.read_text("utf-8")
        count = src.count(old)
        if count != 1:
            results.append(
                MutationResult(
                    gate_id,
                    mut.get("file", ""),
                    False,
                    False,
                    f"`old` appears {count} time(s); the edit must be unambiguous. "
                    f"A mutation that did not apply is NOT a passed mutation test.",
                )
            )
            continue
        try:
            target.write_text(src.replace(old, new, 1), "utf-8")
            outcome, ev = run_command_gate(gdef, cwd)
            detected = outcome == "failed"
            results.append(
                MutationResult(
                    gate_id,
                    mut.get("file", ""),
                    True,
                    detected,
                    ""
                    if detected
                    else f"the gate reported {outcome!r} on mutated source — it cannot "
                    f"see this class of change. {ev.get('reason', '')}"[:300],
                )
            )
        finally:
            target.write_text(src, "utf-8")
    return results, ""


#: How many untracked files `tree_fingerprint` will hash before giving up on content
#: and falling back to their names alone. `--exclude-standard` already drops anything
#: gitignored, so a repository normally has a handful; a run that has just dumped ten
#: thousand artifacts into an un-ignored directory should not turn every gate into a
#: full read of them. Configurable because the right number depends on the project --
#: raise it if your work genuinely spans more new files than this.
MAX_UNTRACKED_HASHED = 512


#: Paths the fingerprint must IGNORE. `.ddflow/` is this tool's own bookkeeping, and
#: recording a gate's outcome writes an event into it — so including it made the
#: fingerprint move as a DIRECT RESULT of taking it, and every completion warned that
#: the tree had changed since the gate ran. The evidence a gate produces is about the
#: SOURCE; the fact that recording it appended to a log is not a reason to distrust it.
#:
#: Expressed as git pathspecs so the exclusion happens inside git rather than by
#: filtering its output afterwards — a post-filter has to re-implement pathspec
#: matching, and would drift from what git itself considers inside the directory.
FINGERPRINT_EXCLUDE: tuple[str, ...] = (":(exclude).ddflow", ":(exclude).ddflow/**")


def _untracked_paths(cwd: Path) -> list[str]:
    """Untracked, non-ignored paths, excluding ddflow's own state. ONE spelling.

    `git ls-files --others --exclude-standard -- . *FINGERPRINT_EXCLUDE` was written out
    twice in this module and run twice per gate. Two copies of an argv list is two
    chances for one of them to forget the exclusion, which is exactly how `.ddflow/`
    crept into a fingerprint and made every completion warn that the tree had moved.
    """
    from ..infra import worktree as W

    r = W.git(cwd, "ls-files", "--others", "--exclude-standard", "--", ".", *FINGERPRINT_EXCLUDE)
    return r.out.splitlines() if r.ok and r.out.strip() else []


def _untracked_digest(cwd: Path) -> str:
    """Content ids for untracked files, or their names when there are too many.

    `git hash-object` WITHOUT `-w`: it computes the object ids and writes nothing to
    the object store, so this stays an observation. Binary content is covered here for
    free, because hash-object hashes bytes and does not care what they are.
    """
    from ..infra import worktree as W

    paths = _untracked_paths(cwd)
    if not paths:
        return ""
    if len(paths) > MAX_UNTRACKED_HASHED:
        # Names only. Degraded, and SAID so in the digest rather than silently: a
        # fingerprint that quietly stopped covering content would make `stale_evidence`
        # go quiet for the repositories that need it most.
        return f"names-only:{len(paths)}:" + digest("\n".join(sorted(paths)))
    hashed = W.git(cwd, "hash-object", *paths)
    ids = hashed.out.splitlines()
    # A short or long reply must not be zipped silently: `zip` would truncate to the
    # shorter list, pairing hashes with the wrong paths and producing a fingerprint
    # that looks entirely plausible and means nothing. Fall back to names, which is
    # weaker but honest.
    if not hashed.ok or len(ids) != len(paths):
        return digest("\n".join(sorted(paths)))
    return digest("\n".join(f"{h} {p}" for h, p in zip(ids, paths, strict=True)))


#: `git diff --numstat` emits three tab-separated fields per changed file:
#: additions, deletions, path.
_NUMSTAT_FIELDS = 3


def diff_stat(cwd: Path) -> dict[str, int]:
    """Files and lines changed against HEAD, plus untracked files. Read-only.

    Deliberately NOT part of `tree_fingerprint`: a fingerprint answers "is this the
    same tree", and two different trees can share a line count. Mixing a human-readable
    magnitude into an identity would make the identity weaker and the magnitude
    unavailable on its own.

    Uses the same `.ddflow/` exclusion as the fingerprint, for the same reason: a
    number that grows every time ddflow records an event describes ddflow's bookkeeping
    rather than the work.

    Returns zeros rather than raising outside a repository. A gate can legitimately run
    where git does not reach, and a missing statistic is honest where a fabricated one
    is not -- but the keys are always present, so a reader never has to distinguish
    "no change" from "the field did not exist yet".
    """
    from ..infra import worktree as W

    out = {"files": 0, "insertions": 0, "deletions": 0, "untracked": 0}
    # Untracked FIRST, and outside the HEAD guard: it needs no commit. A repository
    # before its first commit -- `git init`, scaffold a module, run the test gate -- used
    # to report every field zero, so the one gate run where everything is new reported
    # the smallest possible change.
    untracked = _untracked_paths(cwd)
    out["untracked"] = len(untracked)
    # ...and their LINES count toward the magnitude. `git diff HEAD --numstat` never
    # reports untracked content, so a task that is entirely new files -- the ordinary
    # shape of a new module -- showed 0 insertions while `tree_fingerprint` deliberately
    # hashes exactly those bytes. The two halves of the same evidence disagreed about
    # whether the work existed.
    if len(untracked) <= MAX_UNTRACKED_HASHED:
        for rel in untracked:
            try:
                blob = (Path(cwd) / rel).read_bytes()
            except OSError:
                continue
            if b"\0" in blob[:8000]:
                out["files"] += 1  # binary: a changed file with no line count
                continue
            out["insertions"] += blob.count(b"\n") + (0 if blob.endswith(b"\n") or not blob else 1)
            out["files"] += 1
    if not W.git(cwd, "rev-parse", "HEAD").ok:
        return out
    r = W.git(cwd, "diff", "HEAD", "--numstat", "--", ".", *FINGERPRINT_EXCLUDE)
    if r.ok:
        for line in r.out.splitlines():
            # `--numstat` is exactly: additions, deletions, path. A rename emits the
            # path as `old => new`, which still lands in field three, so splitting on
            # tab and requiring three fields is correct for every form.
            parts = line.split("\t")
            if len(parts) < _NUMSTAT_FIELDS:
                continue
            add, rem = parts[0], parts[1]
            out["files"] += 1
            # `-` for a binary file, which has no line count. Counted as a changed
            # FILE with zero lines rather than skipped, so a commit of nothing but
            # binaries does not report "0 files changed".
            out["insertions"] += int(add) if add.isdigit() else 0
            out["deletions"] += int(rem) if rem.isdigit() else 0
    u = W.git(cwd, "ls-files", "--others", "--exclude-standard", "--", ".", *FINGERPRINT_EXCLUDE)
    if u.ok and u.out.strip():
        out["untracked"] = len(u.out.splitlines())
    return out


def tree_fingerprint(cwd: Path) -> str:
    """What the working tree looked like, committed and uncommitted.

    `HEAD` alone is not enough -- the interesting state during a gate run is almost
    always dirty -- so this is the commit, plus the CONTENT of the tracked changes,
    plus the LIST of everything else git reports.

    Porcelain alone was not enough either, and that was a real bug. `git status
    --porcelain` is two status letters and a path: no content, no size, no mtime. So
    once a file was modified, every further edit to that same file produced
    byte-identical output and the digest did not move. That is not an edge case, it is
    the normal one -- at gate time the agent has been editing all along, so the tree is
    already dirty when the fingerprint is taken and the clean->dirty transition has
    already happened. `stale_evidence`'s own documented scenario ("run the tests, edit
    one more thing, complete") was therefore the case it could not see, and a gate that
    passed on superseded source read as fresh evidence.

    So `git diff HEAD` goes in as well: content-sensitive for tracked files, staged or
    not, and it adds no new noise because untracked paths were already in the porcelain
    listing. Cost is proportional to the SIZE OF THE CHANGES, not to the tree.

    `.ddflow/` is EXCLUDED throughout. Recording a gate outcome appends an event to it,
    so counting it made the fingerprint move as a direct consequence of taking it, and
    every completion then warned that the tree had changed since the gate ran. A
    warning that always fires is one nobody reads — and it would have fired on the
    ordinary path, not an edge case.

    UNTRACKED files are hashed separately, and that is not an optional extra: `git diff
    HEAD` never shows them and porcelain shows only `?? path`, so without this a brand
    new module -- untracked until its first commit, which is the ORDINARY state of
    agent work -- could be rewritten completely between the gate and the completion
    with the fingerprint unmoved. `git hash-object` without `-w` computes the ids and
    writes nothing, so the observer still does not change what it observes.

    **Known limit, stated rather than papered over:** `git diff` renders a TRACKED
    binary file as "Binary files ... differ" with no content, so a re-edit of an
    already-modified tracked binary is invisible. Untracked binaries are covered (they
    are hashed bytewise). The fix for the remaining case -- `--binary`, base85-encoding
    whole blobs into the digest -- costs far more than it buys here. Filed as B95.

    Not `write-tree` or `stash create`: both WRITE, and a function whose job is to
    observe must not change what it observes. Outside a repository it returns ""
    rather than raising, because a gate can legitimately run somewhere git does not
    reach, and a missing fingerprint is honest where a fabricated one is not.
    """
    from ..infra import worktree as W

    head = W.git(cwd, "rev-parse", "HEAD")
    if not head.ok:
        return ""
    status = W.git(cwd, "status", "--porcelain", "--", ".", *FINGERPRINT_EXCLUDE)
    # `HEAD` and not `--cached`: staged and unstaged changes are equally "not what is
    # committed", and a gate cares about the files on disk it just ran against.
    diff = W.git(cwd, "diff", "HEAD", "--", ".", *FINGERPRINT_EXCLUDE)
    parts = [status.out if status.ok else "", diff.out if diff.ok else ""]
    parts.append(_untracked_digest(cwd))
    body = "\x00".join(parts)
    # Each PART, not the joined body: `str.strip` keeps NUL, so a clean tree's body
    # "\0\0" never tested empty and every fingerprint read as dirty
    # (bug B-fingerprint-never-clean). `LEGACY_CLEAN` is what that recorded.
    dirt = digest(body) if any(p.strip() for p in parts) else "clean"
    return f"{head.out.strip()[:12]}+{dirt}"


#: The "dirt" every CLEAN tree recorded before bug B-fingerprint-never-clean was fixed:
#: the digest of the two NULs joining three empty parts. Read as "clean".
LEGACY_CLEAN = "95e0c70caf8cc336"  # == digest("\x00\x00"), pinned by a test


def normal_fingerprint(fp: str) -> str:
    """``fp`` with the pre-fix spelling of a clean tree read as `clean`."""
    base, sep, dirt = fp.partition("+")
    return f"{base}+clean" if sep and dirt == LEGACY_CLEAN else fp


def _git_z(cwd: Path | str, *args: str) -> list[str] | None:
    """NUL-separated git output as names exactly as the filesystem spells them, or None
    when git failed -- "could not tell", never "nothing"."""
    try:
        p = P.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return [os.fsdecode(x) for x in p.stdout.split(b"\0") if x]


def _ours(path: str) -> bool:
    """`.ddflow/` is ddflow's bookkeeping, not the work -- see FINGERPRINT_EXCLUDE."""
    return path == ".ddflow" or path.startswith(".ddflow/")


#: One file's identity inside a content tree: (mode, blob id), as git stores it.
TreeEntries = dict[str, tuple[str, str]]


def commit_tree_entries(cwd: Path | str, rev: str) -> TreeEntries | None:
    """Every path of commit ``rev`` with its mode and blob id, `.ddflow/` left out."""
    rows = _git_z(cwd, "ls-tree", "-r", "-z", "--full-tree", rev)
    if rows is None:
        return None
    out: TreeEntries = {}
    for row in rows:
        meta, _, path = row.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and path and not _ours(path):  # noqa: PLR2004 -- mode type id
            out[path] = (parts[0], parts[2])
    return out


def _blob_id(data: bytes, fmt: str) -> str:
    """git's blob id for ``data``, computed here: `hash-object` would follow a symlink."""
    h = hashlib.new("sha256" if fmt == "sha256" else "sha1")
    h.update(b"blob %d\0" % len(data) + data)
    return h.hexdigest()


def worktree_entries(cwd: Path | str) -> TreeEntries | None:
    """What the working tree holds, as the commit that recorded it exactly would.

    The index's entries, overridden by every file whose working copy differs from the
    index and by every untracked, non-ignored file -- each hashed with `git hash-object`
    WITHOUT `-w`, so nothing is written (the same rule `tree_fingerprint` keeps). Whole
    repository, from its top level, `.ddflow/` excluded.

    None when it cannot be told: not a repository, an unmerged index, or more untracked
    files than MAX_UNTRACKED_HASHED.
    """
    from ..infra import worktree as W

    top = W.git(cwd, "rev-parse", "--show-toplevel")
    if not top.ok:
        return None
    root = Path(top.out)
    index = _git_z(root, "ls-files", "-s", "-z")
    changed = _git_z(root, "diff", "--name-only", "-z", "--no-renames")
    untracked = _git_z(root, "ls-files", "--others", "--exclude-standard", "-z")
    if index is None or changed is None or untracked is None:
        return None
    if len(untracked) > MAX_UNTRACKED_HASHED:
        return None
    out = _index_entries(index)
    if out is None:
        return None
    fmt = W.git(root, "rev-parse", "--show-object-format").out or "sha1"
    filemode = W.git(root, "config", "--bool", "core.fileMode").out != "false"
    to_hash: list[tuple[str, str]] = []
    for path in [*changed, *untracked]:
        if _ours(path):
            continue
        path = path.rstrip("/")  # noqa: PLW2901 -- an untracked nested repository
        kind, value = _working_entry(root, path, out.get(path), fmt, filemode)
        if kind == "drop":
            out.pop(path, None)
        elif kind == "set":
            out[path] = value
        elif kind == "hash":
            to_hash.append((path, value))
    return out if _hash_into(root, out, to_hash) else None


def _working_entry(
    root: Path, path: str, prior: tuple[str, str] | None, fmt: str, filemode: bool
) -> tuple[str, Any]:
    """How ``path``'s working copy enters the tree, as `git add` would record it:
    ("drop", None), ("set", (mode, id)), ("hash", mode) or ("keep", None)."""
    from ..infra import worktree as W

    full = root / path
    if not os.path.lexists(full):
        return "drop", None
    if full.is_symlink():
        return "set", ("120000", _blob_id(os.fsencode(os.readlink(full)), fmt))
    if full.is_dir():
        # A repository (a submodule, or a nested repository `git add` would record as
        # one) is its checked-out commit. Any other directory has REPLACED a tracked
        # file of that name: the file is gone, and what is under the directory arrives
        # as untracked paths of its own.
        if (full / ".git").exists():
            head = W.git(full, "rev-parse", "HEAD")
            return ("set", ("160000", head.out)) if head.ok else ("keep", None)
        return "drop", None
    if filemode:
        # git's own test: the OWNER's execute bit (S_IXUSR), not any of the three.
        return "hash", "100755" if full.stat().st_mode & 0o100 else "100644"
    # core.fileMode=false: git ignores the bit -- a regular file keeps the index's
    # mode; anything else (new, or replacing a symlink or a submodule) is 100644.
    regular = prior is not None and prior[0] in ("100644", "100755")
    return "hash", prior[0] if prior is not None and regular else "100644"


def _index_entries(rows: list[str]) -> TreeEntries | None:
    """`git ls-files -s -z` rows as entries; None for an unmerged index (no one tree)."""
    out: TreeEntries = {}
    for row in rows:
        meta, _, path = row.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path or parts[2] != "0":  # noqa: PLR2004 -- mode id stage
            return None
        if not _ours(path):
            out[path] = (parts[0], parts[1])
    return out


def _hash_into(root: Path, out: TreeEntries, to_hash: list[tuple[str, str]]) -> bool:
    """Blob ids for ``to_hash`` ((path, mode)) into ``out``, without writing objects."""
    from ..infra import worktree as W

    for i in range(0, len(to_hash), 200):
        chunk = to_hash[i : i + 200]
        r = W.git(root, "hash-object", "--", *(p for p, _ in chunk))
        ids = r.out.splitlines()
        if not r.ok or len(ids) != len(chunk):
            return False
        for (path, mode), oid in zip(chunk, ids, strict=True):
            out[path] = (mode, oid)
    return True


def content_id(entries: TreeEntries | None) -> str:
    """One id for a content tree; "" when there is none to name."""
    if entries is None:
        return ""
    body = "\n".join(f"{m} {o} {p}" for p, (m, o) in sorted(entries.items()))
    return (
        "st:" + hashlib.blake2b(body.encode("utf-8", "surrogateescape"), digest_size=16).hexdigest()
    )


def source_tree(cwd: Path | str) -> str:
    """The CONTENT the working tree holds -- every path's mode and blob id.

    `tree_fingerprint` names a commit plus dirt, so the same files under another commit
    (the merge commit, a rebased head, the branch tip after the edits were committed)
    read as a different tree. This names only content: a gate run on uncommitted edits
    has the same source tree as the commit that later records exactly those edits.
    """
    return content_id(worktree_entries(cwd))


def commit_source_tree(cwd: Path | str, rev: str) -> str:
    """`source_tree` of a commit, comparable with one taken from a working tree."""
    return content_id(commit_tree_entries(cwd, rev))


def differing_paths(a: TreeEntries, b: TreeEntries) -> list[str]:
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


#: A test runner's own verdict lines, wherever in the output they fall: pytest's
#: `=== 3 failed, 112 passed in 4.2s ===` banners (bare under `-q`), unittest's `Ran 12 tests in 0.1s`
#: and `FAILED (failures=2)` / `OK (skipped=1)`.
_SUMMARY_LINE = re.compile(
    r"=+ .*\b(passed|failed|errors?|skipped|xfailed|xpassed|deselected|no tests ran)\b.* =+"
    r"|\d+ (passed|failed|errors?|skipped)\b.* in \d[\d.]*s\b.*"  # pytest -q: no banner
    r"|Ran \d+ tests? in \S+"
    r"|(OK|FAILED) \(.*\)"
)
#: How many summary lines are kept: the first and last half of them when there are more.
MAX_SUMMARY_LINES = 20


def summary_lines(out: str) -> list[str]:
    """The suite's own verdict lines from the WHOLE output, not the tail."""
    found = [ln.strip() for ln in out.splitlines() if _SUMMARY_LINE.fullmatch(ln.strip())]
    if len(found) <= MAX_SUMMARY_LINES:
        return found
    half = MAX_SUMMARY_LINES // 2
    return [*found[:half], f"... {len(found) - 2 * half} more ...", *found[-half:]]


#: Where `gate run` keeps each run's full output, under the primary's `.ddflow/`.
#: Machine-local: the directory ignores itself, so no project's .gitignore has to know.
RUNS_DIR = "runs"
#: Run logs kept per item and gate; older ones are deleted when a new one is written.
KEEP_RUN_LOGS = 10


def run_log_writer(repo: Path, item_id: str, gate: str) -> Callable[[str], str]:
    """A `keep_output` for `run_command_gate`: writes the output to
    `.ddflow/runs/<item>/<gate>-<UTC time>-<pid>.log` and returns that path relative to
    ``repo``. The newest KEEP_RUN_LOGS per item and gate are kept."""

    def keep(out: str) -> str:
        runs = Path(repo) / ".ddflow" / RUNS_DIR
        runs.mkdir(parents=True, exist_ok=True)
        ignore = runs / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# gate run output logs: machine-local, never committed\n*\n")
        where = runs / item_id
        where.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = where / f"{gate}-{stamp}-{os.getpid()}.log"
        path.write_text(out, "utf-8", errors="replace")
        old = sorted(where.glob(f"{gate}-*.log"), key=lambda q: q.stat().st_mtime)
        for stale in old[:-KEEP_RUN_LOGS]:
            stale.unlink(missing_ok=True)
        return path.relative_to(repo).as_posix()

    return keep


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=8).hexdigest()


def _run_ticking(
    command: str,
    cwd: str,
    env: dict[str, str],
    timeout_s: float,
    on_tick: Callable[[], None],
    tick_s: float,
) -> subprocess.CompletedProcess:
    """Run a shell command to completion, calling `on_tick` every `tick_s` meanwhile.

    On THIS thread. The first keep-alive for long gates renewed the lease from a
    background thread, and the event log's parse cache and lock bookkeeping are
    process-global and unlocked because nothing in this process was concurrent -- a
    renewal slower than the join timeout could race the caller's own write (roborev
    827). Polling keeps the process single-threaded. Raises TimeoutExpired like
    `subprocess.run`, after killing the command.
    """
    p = P.popen(
        command,
        # bandit B604: the same operator-written gate command `run_command_gate` runs.
        shell=True,  # nosec B604
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + timeout_s
    while True:
        try:
            out, err = p.communicate(timeout=max(0.05, min(tick_s, deadline - time.time())))
            return subprocess.CompletedProcess(command, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            if time.time() >= deadline:
                p.kill()
                p.communicate()
                raise
            on_tick()


def run_command_gate(
    gdef: GateDef,
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 0,
    keep_output: Callable[[str], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Execute a command gate. Returns (outcome, evidence).

    ``keep_output`` stores the WHOLE output and returns where (`gate run` passes
    `run_log_writer`): without it the evidence holds only the tail, and its digest
    names bytes nobody kept.

    The distinction this function exists to preserve: a command that *ran and failed*
    is ``failed``; a command that *could not run* — binary missing, cwd gone, timed out —
    is ``unavailable``. Collapsing them lets a tool that quietly stopped being installed
    read as a suite that quietly started passing.
    """
    if not gdef.is_command_gate:
        reason = "no command configured for this gate"
        hint = suggested_test_command(cwd) if gdef.id == "unit_tests" else ""
        if hint:
            reason += f" -- for this project: ddflow config --set gate.unit_tests.command '{hint}'"
        return "unavailable", {"reason": reason}
    if not cwd.exists():
        return "unavailable", {"reason": f"working directory {cwd} does not exist"}

    # Pre-flight the executable. Under `shell=True` a missing binary exits 127, which
    # is indistinguishable from a test suite that chose to exit 127 -- so a tool that
    # quietly stopped being installed would read as a suite that ran and failed.
    # Resolving it BEFORE running removes the ambiguity at the source. Only attempted
    # for a simple leading token; anything with shell syntax up front is the operator
    # deliberately writing shell, and is left alone.
    missing = _missing_executable(gdef.command)
    if missing:
        return "unavailable", {
            "reason": f"executable {missing!r} is not on PATH -- the gate could not run. "
            f"This is NOT a failing check; install it or reconfigure the gate.",
            "command": gdef.command,
            "missing_executable": missing,
        }
    # No bytecode cache in either direction. CPython invalidates a `.pyc` on the
    # source's mtime and SIZE -- and an agent editing in a loop produces same-second,
    # same-size edits by accident, which leaves a stale cache that looks valid. The run
    # AFTER a patch then executes the code from BEFORE it and reports a pass about
    # source that is no longer there: the worst possible failure for a verification step.
    #
    # PYTHONDONTWRITEBYTECODE alone only stops the gate WRITING a cache; it still READ
    # the one the agent's own test run left in `__pycache__`, and executed stale code
    # (tests/test_gates.py::test_a_gate_never_runs_a_stale_pyc_someone_else_wrote).
    # Pointing PYTHONPYCACHEPREFIX at an empty directory makes the interpreter look
    # there instead, so every module compiles from the source actually on disk.
    #
    # Set AFTER os.environ, so an ambient shell variable cannot quietly undo it, and
    # BEFORE the gate's own env, so an operator can still override it deliberately.
    #
    # The cost is real, and the override is the escape hatch for it: every interpreter
    # the gate starts compiles from source (measured ~0.2s -> ~1.0s for a heavy import
    # set), and a gate that INSTALLS packages (`pip install -e . && pytest`, tox) records
    # bytecode paths inside this throwaway directory in the venv's RECORD. A gate that
    # pays too much sets `env = { PYTHONPYCACHEPREFIX = "" }` -- empty disables the
    # prefix -- and accepts the stale-cache hazard for itself, knowingly.
    with tempfile.TemporaryDirectory(prefix="ddflow-pyc-", ignore_cleanup_errors=True) as no_cache:
        full_env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": no_cache,
            **gdef.env,
            **(env or {}),
        }
        start = time.time()
        try:
            if on_tick is not None and tick_s > 0:
                p = _run_ticking(gdef.command, str(cwd), full_env, gdef.timeout_s, on_tick, tick_s)
            else:
                p = P.run(
                    gdef.command,
                    # bandit B604: a command gate IS a shell command line the operator wrote
                    # in gates.toml.
                    shell=True,  # nosec B604
                    cwd=str(cwd),
                    env=full_env,
                    capture_output=True,
                    text=True,
                    timeout=gdef.timeout_s,
                )
        except subprocess.TimeoutExpired:
            return "unavailable", {
                "reason": f"timed out after {gdef.timeout_s}s",
                "command": gdef.command,
                "elapsed_s": round(time.time() - start, 1),
            }
        except (OSError, ValueError) as exc:
            return "unavailable", {"reason": f"could not execute: {exc}", "command": gdef.command}
    out = (p.stdout or "") + (p.stderr or "")
    # Belt and braces for the compound-command case the pre-flight cannot inspect
    # (pipes, &&, subshells): POSIX reserves 127 for "command not found" and 126 for
    # "found but not executable", and the shell says so on stderr.
    if p.returncode in (126, 127) and _looks_like_not_found(p.stderr or ""):
        return "unavailable", {
            "reason": f"shell reported exit {p.returncode} (command not found / not "
            f"executable): {(p.stderr or '').strip()[:200]}",
            "command": gdef.command,
            "exit": p.returncode,
        }
    ev = {
        "command": gdef.command,
        "exit": p.returncode,
        "elapsed_s": round(time.time() - start, 1),
        "output_digest": digest(out),
        "output_bytes": len(out),
        "tail": out[-2000:],
        # The suite's own verdict lines from ALL of it: a gate that reruns its failures
        # ends on the rerun's "112 passed", and the tail alone hid the first pass's
        # "115 failed" (bug Bac392907b1).
        "summary": summary_lines(out),
        # WHICH tree this is evidence about. Without it "the tests passed" names
        # nothing: a concurrent agent can move the tree underneath a running probe, and
        # one agent editing between two gates makes the earlier gate's evidence describe
        # source that no longer exists.
        "tree_sha": tree_fingerprint(cwd),
        # ...and its CONTENT, independent of which commit it sits on: what `complete`
        # compares against the branch that landed once the worktree is gone.
        "source_tree": source_tree(cwd),
        # HOW MUCH this gate was looking at. `tree_sha` answers "which tree" and is
        # opaque; this answers "how big was the change", which is what makes a recorded
        # pass auditable after the fact. A review gate that passed over 4,000 changed
        # lines in two minutes is a different claim from one that passed over 12, and
        # without this the log cannot tell them apart.
        "diff_stat": diff_stat(cwd),
    }
    if keep_output is not None:
        # The full output the digest is over, so the digest can be checked. A log that
        # cannot be written is said so in the evidence; it does not change the outcome.
        try:
            ev["output_log"] = keep_output(out)
        except OSError as exc:
            ev["output_log_error"] = str(exc)[:200]
    outcome, why = classify_exit(gdef, p.returncode, out)
    if why:
        ev["reason"] = why
    if outcome == "failed":
        # Only a FAILURE is worth the git calls: a pass under drift is main's command
        # passing here, which is what the gate asks, and an unavailable already says so.
        outcome = _account_for_drift(gdef, cwd, p.returncode, out, ev)
    return outcome, ev


#: The `[gate.<id>]` keys that decide what a command gate RUNS and how its exit reads.
#: Only these count as drift: a reworded prompt on main must not turn a real failure on
#: an older branch into "unavailable".
_RUN_FIELDS = frozenset(
    {"command", "cwd", "env", "timeout_s", "unavailable_exits", "partial_exits",
     "require_output", "fail_output"}
)  # fmt: skip


def _run_spec(texts: Iterable[str], gate_id: str) -> dict[str, Any]:
    """The run-deciding part of `[gate.<gate_id>]` across `texts`, later winning."""
    spec: dict[str, Any] = {}
    for text in texts:
        try:
            block = (tomllib.loads(text).get("gate") or {}).get(gate_id)
        except tomllib.TOMLDecodeError:
            continue
        if isinstance(block, dict):
            spec.update(block)
    return {k: v for k, v in spec.items() if k in _RUN_FIELDS}


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except (OSError, ValueError):
        return ""


def gate_config_drift(gate_id: str, tree: Path) -> dict[str, Any]:
    """How the gate definition `load_gates` ran differs from the one `tree` commits.

    `{}` when it does not, or `tree` is the primary, or neither side COMMITTED a change
    since the fork (the difference is an uncommitted edit). Otherwise `kind` says whose
    change it is: "behind" -- the base COMMITTED a new definition since `tree` forked
    and the tree still carries the one it forked with, so its code and dependencies
    predate the command that ran -- or "own", the branch changed the definition itself,
    which merging the base cannot fix.

    Needed because the definition comes from the PRIMARY (`repo_root`) while the command
    runs in the item's tree: main switching unit_tests to `-n 48` alongside adding
    pytest-xdist failed every older branch with `unrecognized arguments: -n 48`,
    recorded as a test failure (bug B8ea7a90aea). The config-knob half of the same
    class warns "merge main" from `config._warn_unknown`.
    """
    from ..infra import tomlcfg
    from ..infra import worktree as W

    try:
        primary = W.repo_root(tree)
    except (W.GitError, OSError):
        return {}
    if primary.resolve() == Path(tree).resolve():
        return {}
    # The layers `load_gates` reads. Every spec compared below carries the PRIMARY's
    # machine-local layer on top: it is git-ignored, applies whichever tree's committed
    # files it lands on, and wins -- so a committed change it overrides never ran, and
    # calling that drift turned a real failure into "unavailable" (bug
    # B-drift-ignores-local-layer).
    paths = tomlcfg.config_paths(primary, "gates.toml")
    committed = [str(p.relative_to(primary)) for p in paths[:2]]
    local = [_read_text(p) for p in paths[2:]]

    def spec(committed_texts: Iterable[str]) -> dict[str, Any]:
        return _run_spec([*committed_texts, *local], gate_id)

    ran = spec(_read_text(primary / f) for f in committed)
    here = spec(_read_text(Path(tree) / f) for f in committed)
    if ran == here:
        return {}
    # The primary's HEAD by SHA: the name `HEAD` means the tree's own HEAD inside `tree`.
    head = W.git(primary, "rev-parse", "HEAD").out
    base = W.git(primary, "rev-parse", "--abbrev-ref", "HEAD").out
    base = base if base and base != "HEAD" else head[:12] or "the primary checkout"
    fork = W.git(tree, "merge-base", "HEAD", head) if head else None
    if not (fork and fork.ok and fork.out):
        return {}

    def at(rev: str) -> dict[str, Any]:
        return spec(W.git(tree, "show", f"{rev}:{f}").out for f in committed)

    # Classified on COMMITS alone. `ran` and `here` include uncommitted edits, and
    # letting them decide mislabelled both ways: an operator's uncommitted tweak in the
    # primary on top of main's committed change hid the drift, and an agent's
    # half-made edit in the tree turned a branch that is behind into one "changing the
    # gate itself" (bug B-drift-dirty-trees).
    #
    # "behind" first: when the base committed a change, the base's definition is what
    # ran, and the tree's code predates it whatever the tree edited itself -- checking
    # "own" first recorded exactly the original bare failure for a branch that had also
    # touched, say, the timeout (bug B-drift-own-masks-behind).
    forked = at(fork.out)
    if at(head) != forked:
        kind = "behind"
    elif at("HEAD") != forked:
        kind = "own"
    else:
        # Neither side committed a change: the difference is an uncommitted edit -- an
        # operator who just set the command. Merging the base would bring nothing, and
        # the command that ran is the one configured: its failure is a failure
        # (tests/test_cli.py).
        return {}
    return {"kind": kind, "base": base, "ran": ran, "tree": here}


def _account_for_drift(gdef: GateDef, cwd: Path, code: int, out: str, ev: dict[str, Any]) -> str:
    """The outcome of a FAILED run once gate-config drift is known; notes it in `ev`.

    Behind: the failure is the tree's age, not its tests -- UNAVAILABLE, naming the
    remedy, as a tool that could not run is. Own: the failure stands (the branch's
    definition takes effect only when it merges), but says which command ran, or the
    author debugs a command their tree no longer contains.
    """
    drift = gate_config_drift(gdef.id, cwd)
    if not drift:
        return "failed"
    ev["gate_config_drift"] = drift
    base = drift["base"]
    last = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    ran = f"`{gdef.command}` exited {code}" + (f": {last[:160]}" if last else "")
    if drift["kind"] == "behind":
        ev["reason"] = (
            f"your branch is behind a gate-config change on {base}: gate {gdef.id!r} ran "
            f"{base}'s definition, which this branch's code and dependencies predate "
            f"({ran}). NOT a test failure: merge {base} into this branch, then re-run."
        )
        return "unavailable"
    ev["reason"] = (
        f"this branch changes gate {gdef.id!r}'s definition, but gates run {base}'s until "
        f"it merges: {ran}. " + (f"{ev['reason']}" if ev.get("reason") else "")
    ).strip()
    return "failed"


def classify_exit(gdef: GateDef, code: int, output: str) -> tuple[str, str]:
    """`(outcome, reason)` for a command that RAN. The reason is "" for a plain pass/fail.

    The gate's declared exit codes and output patterns first, then the POSIX default.
    A pattern that does not compile is UNAVAILABLE naming the knob: a broken verdict
    rule decides nothing, and guessing either way would be a verdict nobody gave.
    """
    if code in gdef.unavailable_exits:
        return "unavailable", (
            f"exit {code} is declared UNAVAILABLE for this gate (unavailable_exits): the "
            f"tool could not do its job. NOT a failing check."
        )
    if code in gdef.partial_exits:
        return "partial", f"exit {code} is declared PARTIAL for this gate (partial_exits)"
    if code != 0:
        return "failed", ""
    try:
        if gdef.fail_output and re.search(gdef.fail_output, output, re.M):
            return "failed", f"exit 0, but the output matches fail_output /{gdef.fail_output}/"
        if gdef.require_output and not re.search(gdef.require_output, output, re.M):
            return "unavailable", (
                f"exit 0, but the output lacks require_output /{gdef.require_output}/: the "
                f"tool did not demonstrably do its job. NOT a pass."
            )
    except re.error as exc:
        return "unavailable", f"gate {gdef.id!r} has an invalid output pattern ({exc})"
    return "passed", ""


_SHELL_META = set(";|&<>()$`\n")


def _missing_executable(command: str) -> str:
    """The leading executable of ``command`` if it is simple and absent, else "".

    Returns "" (meaning "no opinion") for anything that is not a bare leading token:
    an assignment prefix, a pipeline, a subshell, a builtin. Being sure only about
    the easy case is the point -- a heuristic that guessed at compound shell would
    produce false UNAVAILABLEs, which stall a pipeline as surely as a false pass
    corrupts one.
    """
    cmd = command.strip()
    if not cmd or cmd[0] in _SHELL_META:
        return ""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return ""
    if not tokens:
        return ""
    head = tokens[0]
    if any(ch in _SHELL_META for ch in head) or "=" in head:
        return ""
    if head in _SHELL_BUILTINS or "/" in head:
        return ""  # builtins have no PATH entry; paths are checked by exec
    return "" if shutil.which(head) else head


#: Builtins that legitimately have no PATH entry. `exit`/`true`/`false` appear in real
#: gate configs and in this project's own tests.
_SHELL_BUILTINS = frozenset(
    {
        "exit",
        "true",
        "false",
        "cd",
        "echo",
        "test",
        "[",
        ":",
        "set",
        "unset",
        "export",
        "eval",
        "source",
        ".",
        "read",
        "wait",
        "trap",
        "shift",
        "return",
    }
)


def _looks_like_not_found(stderr: str) -> bool:
    low = stderr.lower()
    return "not found" in low or "no such file or directory" in low or "permission denied" in low


# -- a test gate that uses one core --------------------------------------------------
#
# Measured on this repository (research R537ed343e7): the unit_tests gate ran pytest
# serially in 49 minutes on a 192-core machine, and in under a minute with pytest-xdist.
# Every per-item gate waited on that, so the cheapest speed-up in the workflow was a flag.

#: Where a Python project declares what it installs. Read as TEXT, never imported: the
#: question is what the PROJECT's environment will have, and ddflow's own interpreter is
#: a different environment.
_PY_MANIFESTS = (
    "pyproject.toml", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock",
    "setup.cfg", "setup.py", "tox.ini",
)  # fmt: skip

_PYTEST = re.compile(r"(?:^|[\s/])(?:py\.test|pytest)(?:\s|$)")

#: A pytest command that has already chosen its workers: `-n N`, `-nauto`,
#: `--numprocesses`, `--dist`, or xdist switched off with `-p no:xdist` -- the last two
#: are how an operator says serial is deliberate, and deliberate is not advised against.
_XDIST_CHOSEN = re.compile(r"(?:^|\s)(?:-n\s*\S|--numprocesses\b|--dist\b|-p\s*no:xdist\b)")


#: A `#` comment in TOML, INI, requirements and Python alike -- at line start or after
#: whitespace, so the `#egg=` fragment of a requirement URL is not taken for one.
_COMMENT = re.compile(r"(?:^|\s)#.*$", re.MULTILINE)

#: Whole package names: `pytest-xdist-foo` is not pytest-xdist, `pytest-cov` is not
#: pytest; `[tool.pytest.ini_options]` IS pytest configuration, so `.` may border it.
_XDIST_NAME = re.compile(r"(?<![\w.-])pytest[-_]xdist(?![\w-])", re.IGNORECASE)
_PYTEST_NAME = re.compile(r"(?<![\w-])pytest(?![\w-])", re.IGNORECASE)


def _manifest_texts(root: Path) -> list[str]:
    """Each Python manifest's text with comments removed: a commented-out dependency is
    not a declared one."""
    names = [*_PY_MANIFESTS, *sorted(p.name for p in root.glob("requirements*.txt"))]
    out = []
    for name in names:
        try:
            out.append(
                _COMMENT.sub("", (root / name).read_text(encoding="utf-8", errors="replace"))
            )
        except OSError:
            continue
    return out


def declares_xdist(root: Path) -> bool:
    """Whether the project's manifests declare pytest-xdist."""
    return any(_XDIST_NAME.search(t) for t in _manifest_texts(root))


def uses_pytest(root: Path) -> bool:
    """Evidence the project's tests run under pytest: its own files, or a manifest naming it."""
    if (root / "conftest.py").is_file() or (root / "pytest.ini").is_file():
        return True
    return any(_PYTEST_NAME.search(t) for t in _manifest_texts(root))


def runs_pytest_serially(command: str) -> bool:
    return bool(_PYTEST.search(command)) and not _XDIST_CHOSEN.search(command)


def suggested_test_command(root: Path) -> str:
    """The unit_tests command to propose for a pytest project, or "" for any other."""
    if not uses_pytest(root):
        return ""
    return "pytest -q -n auto" if declares_xdist(root) else "pytest -q"


def parallel_test_advice(command: str, root: Path) -> str:
    """One sentence when ``command`` runs pytest on one core, else ""."""
    if not runs_pytest_serially(command):
        return ""
    serial_ok = "`-p no:xdist` records that serial is deliberate"
    if declares_xdist(root):
        return (
            "runs pytest on ONE core although pytest-xdist is declared: add `-n auto` "
            f"(on a very large machine a fixed `-n N` can be faster); {serial_ok}."
        )
    return (
        "runs pytest on ONE core: declare pytest-xdist (e.g. `uv add --dev pytest-xdist`) "
        f"and add `-n auto`; {serial_ok}."
    )


def record(  # noqa: PLR0913 -- the caller's evidence and ddflow's measurements are kept apart on purpose
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    outcome: str,
    *,
    by: str = "",
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    gates: dict[str, GateDef] | None = None,
    human: bool = False,
    measured: dict[str, Any] | None = None,
) -> None:
    """Write a gate outcome to the log, enforcing the evidence contract.

    ``evidence`` is what the CALLER supplied; ``measured`` is what ddflow determined
    itself (the tree fingerprint, the diff size). Only the first can satisfy the
    contract: the measured fields are always present, so counting them made every bare
    pass look evidenced (bug Bbc9a7ee3f2). Both are recorded.

    Rejecting a bare pass at the API boundary is deliberate. If the only thing standing
    between "I ran the tests" and a recorded pass is the agent's honesty, then over a
    long run the record measures honesty rather than testing.
    """
    if outcome not in GATE_OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}; expected one of {GATE_OUTCOMES}")
    gdef = (gates or {}).get(gate)
    # Refused HERE, at the service boundary, not in the CLI branch that happens to be
    # the usual caller. A check that lives in one surface is a check the other surface
    # does not have, which is how `ddflow_gate_record` would have cleared a human
    # checkpoint over MCP while the terminal refused it.
    if gdef is not None and gdef.is_human_gate and not human:
        raise ValueError(
            f"{gate!r} is a human-approval gate: it is cleared by a person, not by an "
            f"agent recording that it happened. Ask the operator to run "
            f"`ddflow approve {item_id} {gate}` (or `--reject --reason ...`). "
            f"There is deliberately no MCP tool for this."
        )
    if outcome == "skipped":
        if not cfg.gates.allow_skip_with_reason:
            raise ValueError("skipping is disabled ([gates].allow_skip_with_reason)")
        if not reason:
            raise ValueError("a skip must carry --reason; an unexplained skip is invisible")
    if outcome == "passed" and gdef and gdef.evidence and not evidence:
        raise ValueError(
            f"gate {gate!r} requires evidence to pass (it is in gates.evidence_required). "
            f"Attach the command, its exit code and its output — or record "
            f"`unavailable` with a reason, which is an honest result."
        )
    if outcome in ("unavailable", "partial", "failed") and not reason:
        raise ValueError(f"outcome {outcome!r} must carry a --reason")
    log.append(
        f"gate.{outcome}",
        item_id,
        {
            "gate": gate,
            "by": by or log.agent_id,
            "reason": reason,
            "evidence": {**(measured or {}), **(evidence or {})},
        },
    )


def family_of(model: str, cfg: Config) -> str:
    """This project's view of a model's family: `[agent].families`, which defaults to
    the shipped map. ``""`` means "not recognised" — see `config.family_for`."""
    from ..config import family_for

    return family_for(model, cfg.agent.families)


def _declared_family(evidence: dict[str, Any]) -> str:
    """The family `ddflow review` recorded from the operator's reviewer entry, or ``""``.

    A served model name can belong to another family -- this project's Qwen critic is
    served as `google/gemma-4-31B-it` -- which is why a reviewer entry declares one
    (B6ed8b9edb8). Trusted only in evidence `ddflow review` wrote (it names the
    `reviewer`); an agent's `gate record` cannot write a family, so a manual record is
    judged by its model name as before.
    """
    if not evidence.get("reviewer"):
        return ""
    fam = str(evidence.get("family") or "").strip().lower()
    return "" if fam == "unknown" else fam


def reviewer_independence(
    state: State, cfg: Config, item_id: str, author_model: str
) -> tuple[bool, str]:
    """Did at least one reviewer come from a different pretraining family?

    Returns (satisfied, explanation). A same-family panel is reported as unsatisfied
    even when every reviewer passed, because agreement among models trained on the same
    distribution measures shared priors, not correctness.
    """
    from ..config import router_set

    it = state.items.get(item_id)
    if not it:
        return False, f"no such item {item_id}"
    # A router author (Copilot's HydraFusion) is a SET of families: any of them may
    # have written the diff, so a reviewer must sit outside all of them.
    routed = router_set(author_model, cfg.agent.routers)
    # Compared case-blind on BOTH sides: a map value 'Alibaba' and a declared
    # 'ALIBABA' are one family (B-fam-case).
    author_fam = family_of(author_model, cfg).strip().lower() if routed is None else ""
    fams: list[tuple[str, str]] = []
    anonymous: list[str] = []
    for gname in ("rubber_duck", "critic", "standards"):
        rec = it.gates.get(gname)
        if not rec or rec.outcome not in ("passed", "failed", "partial"):
            continue
        m = str(rec.evidence.get("model", rec.by) or "").strip()
        fam = _declared_family(rec.evidence) or family_of(m, cfg).strip().lower()
        # An UNIDENTIFIED reviewer cannot establish independence — see `family_of`.
        if not fam:
            anonymous.append(f"{gname}={m or 'no model'}")
            continue
        fams.append((gname, fam))
    if routed == []:
        return False, (
            f"the author's model {author_model!r} is a router in [agent].routers with "
            f"no families listed, so no reviewer can be shown to sit outside the models "
            f"it drew on. List every family your plan routes it to, e.g. "
            f'routers = {{ hydrafusion = ["anthropic", "openai", "google"] }}.'
        )
    if not author_model.strip():
        # Quoting `''` back at the agent named nothing it could act on (B7a5c63e3d2).
        return False, (
            "the author's model is unknown, so no reviewer can be shown to differ from "
            "it: pass `--model <author model>` (`model` over MCP), or declare it once "
            "with `ddflow session start --model <author model>` under the same identity."
        )
    if routed is None and not author_fam:
        return False, (
            f"the author's model {author_model!r} is not in [agent].families, so no "
            f"reviewer can be shown to differ from it. Add it to the map (or, for a "
            f"model that routes across providers, to [agent].routers with the families "
            f"it draws on), or pass `--model` with a name the map recognises."
        )
    # `router_set` returns its members stripped and lowercased, so the router side is
    # case-blind too: `["Anthropic"]` against a reviewer resolved to 'anthropic' must
    # overlap, or a reviewer from inside the set would pass as independent.
    author_set = routed if routed is not None else [author_fam]
    author_desc = author_fam if routed is None else f"{author_model} ({', '.join(author_set)})"
    if not fams:
        if anonymous:
            return False, (
                f"no reviewer named a model this project recognises "
                f"({', '.join(anonymous)}), so nothing shows the review came from a "
                f"different family than the author ({author_desc}). Re-record with "
                f"`--model <the reviewer's model>`, or teach [agent].families the name."
            )
        return False, "no reviewer ran at all"
    different = [(g, f) for g, f in fams if f not in author_set]
    if different:
        return True, f"{different[0][0]} was {different[0][1]} vs author {author_desc}"
    if routed is not None:
        return False, (
            f"every identified reviewer was inside the families the router "
            f"{author_model!r} draws on: "
            + ", ".join(f"{g}={f}" for g, f in fams)
            + f" overlaps [agent].routers ({', '.join(author_set)}). A reviewer from a "
            f"family the router used may be reviewing its own work."
            + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
        )
    return False, (
        f"every identified reviewer ({', '.join(g for g, _ in fams)}) was family "
        f"{author_fam!r}, the same as the author. Same-family agreement is not "
        f"independent evidence."
        + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
    )


def approve(
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    *,
    gates: dict[str, GateDef] | None = None,
    note: str = "",
    reject: bool = False,
    reason: str = "",
) -> str:
    """A PERSON clears (or refuses) a human gate. Returns the line to print.

    Records the OS user rather than the agent id, and stamps `human: true` on the
    evidence. Neither makes forgery impossible — an agent with a shell can run this —
    but both make a forged approval *visible* in the log instead of identical to a real
    one, which is the difference between a record you can audit and one you cannot.

    A rejection is a first-class outcome, not the absence of an approval: "the operator
    looked and said no" and "nobody has looked yet" are different states, and an item
    sitting in the second forever is how a checkpoint becomes a silent stall.
    """
    gdef = (gates or {}).get(gate)
    if gdef is None:
        raise ValueError(f"no such gate {gate!r}")
    if not gdef.is_human_gate:
        raise ValueError(
            f"{gate!r} is not a human-approval gate, so there is nothing for a person "
            f"to approve. Set `[gate.{gate}] human = true` in .ddflow/gates.toml if it "
            f"should be one; otherwise use `ddflow gate record`."
        )
    if reject and not reason:
        raise ValueError("a rejection must carry --reason: 'no' with no reason cannot be acted on")

    try:
        who = getpass.getuser()
    except Exception:
        # Not fatal, and not silently blank: an approval whose approver is unknown is
        # still a real approval, and saying "unknown" is honest where inventing a name
        # would not be.
        who = "unknown-user"
    ev = {
        "human": True,
        "approved_by": who,
        "host": socket.gethostname().split(".")[0],
        "note": note,
    }
    outcome = "failed" if reject else "passed"
    record(
        log,
        cfg,
        item_id,
        gate,
        outcome,
        by=who,
        reason=reason,
        evidence=ev,
        gates=gates,
        human=True,
    )
    verb = "REJECTED" if reject else "approved"
    tail = f" — {reason}" if reason else (f" — {note}" if note else "")
    return f"{item_id}.{gate} {verb} by {who}{tail}"
