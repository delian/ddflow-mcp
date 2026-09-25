"""The workflow THIS project actually runs — described, checked, and editable.

Three things existed and none of them answered "what are the rules here":
`orchard gate status <id>` shows one item's position in the pipeline; `orchard config
--explain` prints ~60 flat knobs with `gates.task_pipeline` rendered as a Python list;
`orchard status` counts the queue. The only place that ever joined the pipeline, the
gates, the companions and the setup gaps was the MCP handshake — computed once at
`initialize`, unreachable from a terminal, and deliberately lossy.

So an operator asking "why did `complete` refuse?" or "what will this make my agent do?"
had to read the config and the gate table and hold the join in their head.

Two halves here, and the split matters:

* `describe()` **reads** — every rule in force, and where each came from.
* `check()` **judges** — the ways a pipeline can be incoherent, none of which anything
  checked before. The worst is silent and permanent: a pipeline naming a gate id that
  has no definition. `status()` folds the unknown id to `""`, `require_outcome`
  defaults to True so `complete` blocks on it forever, and `gate record` refuses that
  id as unknown — so the item cannot be completed at all except with `--force`, and
  nothing anywhere says why. One typo in a pipeline bricks every item that enters it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from .gates import GateDef, inert_requirements

#: A command gate with no registered mutation has never been shown able to go red.
#: Advisory, not a defect: `orchard gate verify` is how you find out, and a project may
#: reasonably not have got there yet.
ADVISORY = "advisory"
#: Something that will refuse, block or silently pass. These are defects.
PROBLEM = "problem"


@dataclass
class GateView:
    """One gate as this project has configured it, not as it ships."""

    id: str
    kind: str  # "command" | "agent" | "undefined"
    in_task: bool = False
    in_phase: bool = False
    position: int = 0
    required: bool = False
    evidence: bool = False
    reviewer: str = ""
    command: str = ""
    prompt: str = ""
    provable: bool = False  # has registered mutations, so `gate verify` can judge it
    cwd: str = ""
    timeout_s: int = 0

    @property
    def defined(self) -> bool:
        return self.kind != "undefined"


@dataclass
class Finding:
    level: str  # PROBLEM | ADVISORY
    subject: str
    detail: str

    def render(self) -> str:
        return f"[{self.level}] {self.subject}: {self.detail}"


@dataclass
class WorkflowView:
    task_pipeline: list[str] = field(default_factory=list)
    phase_pipeline: list[str] = field(default_factory=list)
    gates: list[GateView] = field(default_factory=list)
    #: knob -> (value, where it came from). Straight from `Config.explain`, so a reader
    #: can tell a deliberate choice from a default nobody has touched.
    rules: dict[str, tuple[Any, str]] = field(default_factory=dict)
    reviewers: list[dict[str, str]] = field(default_factory=list)
    cadences: list[str] = field(default_factory=list)
    overridden_prompts: list[str] = field(default_factory=list)
    hook_installed: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level == PROBLEM]


#: The knobs that describe the WORKFLOW rather than the machinery, in reading order.
#: Named explicitly because `config --explain` already prints all ~60 and the point of
#: this view is that it does not.
RULE_KEYS: tuple[str, ...] = (
    "gates.required",
    "gates.require_outcome",
    "gates.enforce_order",
    "gates.unavailable_is_failure",
    "gates.allow_skip_with_reason",
    "gates.evidence_required",
    "schedule.max_parallel_tasks",
    "worktree.max_parallel",
    "worktree.enabled",
    "lease.ttl_s",
    "enforce.commit_without_lease",
    "enforce.require_item_trailer",
    "agent.reviewer_family_must_differ",
)


def check(cfg: Config, gates: dict[str, GateDef]) -> list[Finding]:
    """Every way this project's pipeline is incoherent. Empty means it hangs together.

    Reported, never raised: a workflow that is wrong in one place should still let
    `orchard workflow` and `orchard doctor` run and explain themselves. Raising at load
    time would mean the one command that could diagnose the problem is the one that
    cannot start.
    """
    out: list[Finding] = []
    for kind, pipeline in (
        ("task", cfg.gates.task_pipeline),
        ("phase", cfg.gates.phase_pipeline),
    ):
        if not pipeline:
            # The editor already refuses `workflow pipeline task ""` as "a project with
            # no checks at all". Saying nothing about the same state when READING it is
            # two surfaces disagreeing about one config, which teaches an operator to
            # believe neither.
            out.append(
                Finding(
                    PROBLEM,
                    f"{kind}_pipeline",
                    f"no gates at all, so nothing checks a {kind} before it completes. "
                    f"The editor refuses to WRITE this; reading it has to say the same.",
                )
            )
        for gid in sorted({g for g in pipeline if pipeline.count(g) > 1}):
            out.append(
                Finding(
                    PROBLEM,
                    f"{kind}_pipeline",
                    f"{gid!r} appears twice. A gate carries ONE outcome, so the second "
                    f"position can never be satisfied separately -- and the report "
                    f"de-duplicates, so nothing showed you it was there.",
                )
            )
        for gid in pipeline:
            if gid in gates:
                continue
            near = [g for g in sorted(gates) if g.startswith(gid[:3]) or gid.startswith(g[:3])]
            out.append(
                Finding(
                    PROBLEM,
                    f"{kind}_pipeline",
                    f"{gid!r} has no gate definition."
                    + (f" Did you mean {near[0]!r}?" if near else "")
                    + f" Every {kind} that enters this pipeline will block on it "
                    f"forever: the outcome folds to empty, `require_outcome` refuses "
                    f"the completion, and `gate record {gid}` refuses the id as "
                    f"unknown. Define it with `orchard workflow gate {gid} ...`, or "
                    f"take it out of the pipeline.",
                )
            )
    for gid in inert_requirements(cfg):
        out.append(
            Finding(
                PROBLEM,
                "gates.required",
                f"{gid!r} is required but is in neither pipeline, so the requirement "
                f"quietly disappears rather than being enforced.",
            )
        )
    # `applies_to` was written by the editor, advertised in the MCP tool description,
    # and read by NOTHING -- a dead knob the surface promised agents they could use.
    # Reading it here is what makes the promise true, and a gate scoped to phases
    # sitting in the task pipeline is a real incoherence either way.
    for kind, pipeline in (
        ("task", cfg.gates.task_pipeline),
        ("phase", cfg.gates.phase_pipeline),
    ):
        for gid in pipeline:
            g = gates.get(gid)
            if g is None or g.applies_to in ("", "both", kind):
                continue
            out.append(
                Finding(
                    PROBLEM,
                    gid,
                    f"declares applies_to = {g.applies_to!r} but is in the {kind} "
                    f"pipeline. Either it applies to {kind}s or it does not belong here.",
                )
            )

    piped = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
    for gid in sorted(piped):
        g = gates.get(gid)
        if g is None:
            continue  # already reported above
        if not g.command.strip() and not g.prompt.strip():
            out.append(
                Finding(
                    PROBLEM,
                    gid,
                    "neither a command nor a prompt, so an agent reaching it is told "
                    "nothing about what to do and a human reviewing the evidence has "
                    "no contract to check it against.",
                )
            )
        elif g.command.strip() and not g.mutations:
            out.append(
                Finding(
                    ADVISORY,
                    gid,
                    "no registered mutations, so nothing has shown this gate can go "
                    f"red. `orchard gate verify <item> {gid}` is how you find out.",
                )
            )
    return out


def describe(
    repo: Path,
    cfg: Config,
    gates: dict[str, GateDef],
    *,
    reviewers: list[Any] | None = None,
    cadences: list[str] | None = None,
) -> WorkflowView:
    """The rules in force here, joined into one answer."""
    v = WorkflowView(
        task_pipeline=list(cfg.gates.task_pipeline),
        phase_pipeline=list(cfg.gates.phase_pipeline),
    )
    sources = {k: s for k, _val, s, _doc in cfg.explain()}
    values = {k: val for k, val, _s, _doc in cfg.explain()}
    v.rules = {k: (values.get(k), sources.get(k, "default")) for k in RULE_KEYS if k in values}

    seen: list[str] = []
    for gid in list(cfg.gates.task_pipeline) + list(cfg.gates.phase_pipeline):
        if gid not in seen:
            seen.append(gid)
    for gid in seen:
        g = gates.get(gid)
        in_task = gid in cfg.gates.task_pipeline
        view = GateView(
            id=gid,
            kind="undefined" if g is None else ("command" if g.is_command_gate else "agent"),
            in_task=in_task,
            in_phase=gid in cfg.gates.phase_pipeline,
            position=(cfg.gates.task_pipeline.index(gid) + 1) if in_task else 0,
            required=gid in cfg.gates.required,
            evidence=gid in cfg.gates.evidence_required,
        )
        if g is not None:
            view.reviewer = g.reviewer
            view.command = g.command
            view.prompt = g.prompt
            view.provable = bool(g.mutations)
            view.cwd = g.cwd
            view.timeout_s = g.timeout_s
        v.gates.append(view)

    for r in reviewers or []:
        v.reviewers.append(
            {
                "name": getattr(r, "name", ""),
                "model": getattr(r, "model", ""),
                "family": getattr(r, "family", ""),
            }
        )
    v.cadences = list(cadences or [])

    local = repo / ".orchard" / "prompts"
    if local.is_dir():
        v.overridden_prompts = sorted(p.stem for p in local.rglob("*.md"))
    hook = repo / ".git" / "hooks" / "pre-commit"
    v.hook_installed = hook.is_file()

    v.findings = check(cfg, gates)
    return v
