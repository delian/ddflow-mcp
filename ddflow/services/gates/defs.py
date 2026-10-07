"""Gate definitions: the built-in pipeline, `.ddflow/gates.toml` loading and which pipeline an item runs."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

from ...config import Config, _is_code_tree
from ...core.model import Item

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
    "verify": GateDef(
        id="verify",
        title="Completion verification",
        evidence=True,
        reviewer="different_family",
        applies_to="task",
        description="An independent model judges whether a DONE task's evidence meets its requirement.",
        prompt=(
            "An independent model judges whether what landed meets the requirement. "
            "ddflow performs it: `ddflow verify <id> --judge` builds the evidence pack and "
            "sends it, with the landed commit, to the configured cross-family reviewer. "
            "Optional, never part of the default pipeline: the second opinion after the "
            "mechanical `ddflow verify <id>`. A finding is a requirement clause the evidence "
            "does not show as met; unavailable is recorded as unavailable, never as a pass."
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
            "language rules does not know your types' operator overloads. Review an "
            "EXPLICIT commit: `roborev review <sha>` with your branch head's sha, never "
            "`roborev review HEAD` from a worktree (it can enqueue the primary's HEAD), "
            "and record it with `gate record --reviewed-sha <sha>`: ddflow refuses a sha "
            "that is not your branch."
        ),
    ),
    "ci": GateDef(
        id="ci",
        title="CI parity",
        evidence=True,
        applies_to="both",
        command="ddflow ci run",
        timeout_s=3600,
        unavailable_exits=[2],
        description="The exact pre-push/CI checks pass on the branch merged with the base.",
        prompt=(
            "Run `ddflow ci run`: it merges your branch with the base in a scratch worktree "
            "and runs the project's pre-push stage there (ruff check, ruff format --check, "
            "bandit, the build probe, the tests ...). Exit 2 means it could not run "
            "(pre-commit missing, nothing configured) and is recorded unavailable, never a "
            "pass. Not part of the default pipeline: add it with `ddflow workflow pipeline "
            "task ...` or `[gates].task_pipeline`, between standards and unit_tests."
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


#: (root, gate) whose `[gate.<id>] required` was already warned about in this process: a
#: long-lived MCP server serves several roots, and one root's warning must not hide
#: another's.
_REQUIRED_WARNED: set[tuple[str, str]] = set()


def _required_in_gate_table(root: Path, gid: str, value: object, lenient: bool) -> None:
    """`[gate.<id>] required` is not read: what is required is `[gates].required`, which
    every enforcement point (status, complete, verify, workflow) reads (B4d206ede45). It
    used to be accepted and ignored, so a gate its own table marked required was never
    enforced. Refused in ddflow's own tree, where config and code are one commit; elsewhere
    warned about once per process and root -- every CLI command -- and skipped (an older
    checkout's config must not stop it)."""
    why = (
        f"[gate.{gid}] sets required = {value!r}, which ddflow does not read: list {gid!r} "
        f"in [gates].required in .ddflow/config.toml instead"
    )
    if not lenient:
        raise ValueError(why)
    if (str(root), gid) not in _REQUIRED_WARNED:
        _REQUIRED_WARNED.add((str(root), gid))
        print(f"ddflow: warning: {why}; skipped.", file=sys.stderr)


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
    from ...infra import tomlcfg

    # A newer checkout's gate field warns and is skipped by older code (B0016a65167).
    lenient = not _is_code_tree(root)
    gates = {k: GateDef(**{**v.__dict__}) for k, v in DEFAULT_GATES.items()}
    for gid, spec in tomlcfg.overlay_table(
        tomlcfg.config_paths(root, "gates.toml"), "gate", GateDef, lenient=lenient
    ).items():
        if "required" in spec:
            _required_in_gate_table(root, gid, spec.pop("required"), lenient)
        base = gates.get(gid) or GateDef(id=gid)
        for k, v in spec.items():
            setattr(base, k, v)
        base.id = gid
        gates[gid] = base
    # A human checkpoint the COMMITTED files declare cannot be switched off by the
    # git-ignored local layer: a `human = false` there never appears in a diff or a
    # review, so it would quietly hand the operator's gate to any agent on this machine.
    committed = tomlcfg.config_paths(root, "gates.toml")[:2]
    for gid, spec in tomlcfg.overlay_table(committed, "gate", GateDef, lenient=lenient).items():
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


def pipelines(cfg: Config, *, running: bool = False) -> dict[str, list[str]]:
    """Every gate pipeline, by name (`task`, `phase`, `promotion`, ...).

    Derived from the `gates.*_pipeline` fields rather than listed: naming task and phase
    missed `promotion_pipeline` -- where a deploy sign-off belongs -- so a gate required
    only there read as "in neither pipeline" (B7f0b7c8839, and Be14f271da8 before it),
    and a list would miss the next pipeline the same way.

    ``running`` keeps only the pipelines items here can actually run: the promotion
    pipeline runs only where `flow.environments` exist, so without any a gate only it
    names enforces nothing (roborev on fix-B7f0b7c8839).
    """
    out = {
        f.name.removesuffix("_pipeline"): list(getattr(cfg.gates, f.name) or ())
        for f in fields(cfg.gates)
        if f.name.endswith("_pipeline")
    }
    if running and not cfg.flow.environments:
        out.pop("promotion", None)
    return out


def pipelined(cfg: Config, *, running: bool = False) -> set[str]:
    """Every gate id some pipeline names (``running``: some pipeline actually runs)."""
    return {g for ids in pipelines(cfg, running=running).values() for g in ids}


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
    return sorted(set(cfg.gates.required) - pipelined(cfg, running=True))
