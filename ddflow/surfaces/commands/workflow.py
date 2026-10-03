"""`ddflow workflow ...` — the human surface for `api.workflow`.

The prose renderer is the whole reason this module is separate from the api one: "what
is my workflow" is a question a person asks and reads once, and the answer is a page of
text with reasoning in it. The MCP tool returns the same answer as JSON. Both come from
one call.
"""

from __future__ import annotations

import json
import sys

from ...api import workflow as A
from ..context import FAIL, NOTHING, Ctx


def _report(c: Ctx, out, human: str, payload: str | tuple[str, ...] = "") -> int:
    """Print one or the other and return the Outcome's exit, unchanged.

    `out.body(payload)` is the same projection the MCP tool uses — that shared call is
    what makes "the two surfaces agree" a property rather than a habit.
    """
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(human, out.body(payload))
    return out.exit


def _workflow_show(a, c: Ctx) -> int:
    out = A.show(c.repo)
    if c.json:
        print(json.dumps(out.data, indent=2, default=str))
        return out.exit
    print(_render_workflow(A.view_for_render(c.repo)))
    return out.exit


def _gate_lines(v) -> list[str]:
    """Each gate in the task pipeline, with what is true about it.

    The marks matter more than they look: "NOT proven able to fail" is a gate whose
    command has never been observed returning non-zero, which is the vacuous-pass class
    stated to the reader's face.
    """
    out: list[str] = []
    for g in v.gates:
        if not g.in_task:
            continue
        marks = []
        if g.required:
            marks.append("required")
        if g.evidence:
            marks.append("evidence required")
        if g.reviewer == "different_family":
            marks.append("needs a different-family reviewer")
        if g.kind == "command":
            marks.append("proven able to fail" if g.provable else "NOT proven able to fail")
        if g.kind == "undefined":
            marks.append("UNDEFINED")
        tail = f"  ({', '.join(marks)})" if marks else ""
        out.append(f"  {g.position:>2}. {g.id:<14}{g.kind:<10}{tail}")
        if g.command:
            out.append(f"      $ {g.command}")
        elif g.prompt:
            first = g.prompt.strip().splitlines()[0] if g.prompt.strip() else ""
            out.append(f"      asks: {first[:96]}")
    return out


def _rule_lines(v) -> list[str]:
    """Every rule, its value, and WHERE it came from — default, config, or env.

    `enforce_order` additionally carries how often it has actually fired, because a rule
    that has never fired on a real recording is indistinguishable from one that does not
    work, and this is the only place anyone would notice.
    """
    out: list[str] = []
    for key, (value, source) in v.rules.items():
        line = f"  {key:<38} {value!s:<28} [{source}]"
        if key == "gates.enforce_order":
            rate = v.order_violation_rate
            line += (
                "  — no gate recorded yet"
                if rate is None
                else f"  — fired on {v.order_violations} of {v.order_recordings} "
                f"recording(s), {rate:.0%}"
            )
        out.append(line)
    return out


def _reviewer_lines(v) -> list[str]:
    if not v.reviewers:
        return [
            "## Reviewers: none configured — the `critic` gate cannot run.",
            "   `ddflow reviewers detect --write` finds one.",
            "",
        ]
    out = ["## Reviewers", ""]
    out += [f"  {r['name']:<20} {r['model']:<32} family={r['family'] or '?'}" for r in v.reviewers]
    out.append("")
    return out


def _finding_lines(v) -> list[str]:
    if not v.findings:
        return [
            "Nothing incoherent: every gate in a pipeline is defined, every",
            "required gate is in one, and each has a command or a prompt.",
            "",
        ]
    return ["## What does not hang together", "", *(f"  {f.render()}" for f in v.findings), ""]


def _render_workflow(v) -> str:
    """The rules in force, as prose a human reads once and an agent can act on.

    Split into per-section helpers when it moved out of `cli.py`, which carries a
    blanket `C901` exemption for `build_parser` — so this function had been 17 branches
    for a while with nothing saying so. The exemption was hiding the complexity of every
    function that happened to live in the same file, which is its own argument for B36.
    """
    out = [
        "# The workflow this project runs",
        "",
        "Every task passes through these gates, in order. An item cannot be",
        "completed until each carries an outcome.",
        "",
        *_gate_lines(v),
        "",
        f"A phase passes through: {', '.join(v.phase_pipeline)}",
        "",
        "## The rules, and where each came from",
        "",
        *_rule_lines(v),
        "",
        *_reviewer_lines(v),
    ]
    if v.overridden_prompts:
        out += [f"## Rewritten locally: {', '.join(v.overridden_prompts)}", ""]
    out += [
        f"Commit hook: {'installed' if v.hook_installed else 'not installed'}",
        "",
        *_finding_lines(v),
        "Change any of it with `ddflow workflow pipeline|gate|drop`, or by",
        "editing .ddflow/config.toml. Both are validated before anything is",
        "written. " + ("" if not v.findings else "Fix the problems above first."),
    ]
    return "\n".join(out)


def _workflow_pipeline(a, c: Ctx) -> int:
    out = A.pipeline(c.repo, a.which, a.gates, dry_run=a.dry_run)
    verb = "would set" if a.dry_run else "set"
    return _report(
        c,
        out,
        f"{verb} {out.data.get('key')} = {', '.join(out.data.get('gates') or [])}",
        ("key", "gates", "applied"),
    )


def _workflow_gate(a, c: Ctx) -> int:
    out = A.gate(
        c.repo,
        A.GateEdit(
            id=a.id,
            command=a.command or "",
            prompt=a.prompt or "",
            cwd=a.cwd or "",
            reviewer=a.reviewer or "",
            title=a.title or "",
            timeout=int(a.timeout or 0),
            applies_to=a.applies_to or "",
            into=a.into or "",
            after=a.after or "",
            required=bool(a.required),
        ),
        dry_run=a.dry_run,
    )
    verb = "would configure" if a.dry_run else "configured"
    return _report(
        c,
        out,
        f"{verb} gate {a.id}: " + ", ".join(out.data.get("changed") or []),
        ("gate", "changed", "applied"),
    )


def _workflow_drop(a, c: Ctx) -> int:
    out = A.drop(c.repo, a.id, dry_run=a.dry_run)
    if out.exit == NOTHING:
        print(out.reason, file=sys.stderr)
        return NOTHING
    verb = "would drop" if a.dry_run else "dropped"
    return _report(
        c,
        out,
        f"{verb} {a.id} from: {', '.join(out.data.get('removed_from') or [])}. Its "
        f"[gate.{a.id}] definition is left in place — put it back with "
        f"`ddflow workflow gate {a.id} --into task`.",
        ("gate", "removed_from", "applied"),
    )


def _workflow_state(a, c: Ctx) -> int:
    """Report comprehensive workflow state."""
    from ...api.workflow_state import workflow_state

    out = workflow_state(c.repo)
    if c.json:
        print(json.dumps(out.data, indent=2, default=str))
        return out.exit

    # Render prose overview
    overview = out.data.get("overview", {})
    lines = [
        "# Workflow State Overview",
        "",
        "## Configuration",
        f"- Flow type: {overview.get('workflow', {}).get('type')}",
        f"- Max parallel: {overview.get('workflow', {}).get('max_parallel_tasks')}",
        "",
        "## Rules",
        f"- Total: {overview.get('rules', {}).get('total', 0)}",
        f"- By scope: {overview.get('rules', {}).get('by_scope', {})}",
        "",
        "## Architecture Decisions",
        f"- Active: {overview.get('decisions', {}).get('active', 0)}",
        "",
        "## Active Work",
        f"- Leases: {overview.get('active_work', {}).get('active_leases', 0)}",
        f"- In progress: {overview.get('active_work', {}).get('items_in_progress', 0)}",
        "",
        "## Project",
        f"- Phases: {overview.get('project', {}).get('phases', 0)}",
        f"- Tasks: {overview.get('project', {}).get('tasks', {})}",
        f"- Open bugs: {overview.get('project', {}).get('bugs_open', 0)}",
        "",
        "## Queue",
        f"- Ready: {overview.get('queue', {}).get('ready_count', 0)}",
        f"- Blocked: {overview.get('queue', {}).get('blocked_count', 0)}",
        "",
        "## Discovery",
        "Ask for more details:",
    ]

    for hint in overview.get("discovery_hints", []):
        lines.append(f"  - {hint}")

    c.out("workflow state", "\n".join(lines))
    return out.exit


def cmd_workflow(a, c: Ctx) -> int:
    """`ddflow workflow` — the rules in force here, and how to change them."""
    return {
        "pipeline": _workflow_pipeline,
        "gate": _workflow_gate,
        "drop": _workflow_drop,
        "state": _workflow_state,
    }.get(a.workflow_cmd or "", _workflow_show)(a, c)
