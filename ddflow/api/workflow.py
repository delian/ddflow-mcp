"""The workflow in force here, and the operations that change it.

`describe()` in `services/workflow.py` already computes the whole picture; this layer's
job is to turn it into an `Outcome` so the CLI's prose and the MCP tool's JSON are two
renderings of ONE answer. They were not: `_workflow_show` assembled a JSON payload and a
separate prose renderer, and `coherent` — whether the configured workflow hangs together
at all — was computed once per surface.

The three WRITE operations are validated by `services/configwrite.write_config`, which
refuses before it writes. The validation lives there, not here, because it is the same
validation whether the change arrives from `ddflow config --set`, a workflow subcommand
or an MCP tool, and a rule enforced in three places is a rule enforced in two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.plain import plain as _plain
from ..services.configwrite import _write_config
from ._base import _load


def _view(repo: Path):
    from ..services import workflow as WF
    from ..services.gates import load_gates
    from ..services.review import load_reviewers

    _log, cfg, st = _load(repo)
    try:
        reviewers = load_reviewers(repo)
    except Exception:
        # A malformed reviewer block must not stop the description: "what is my
        # workflow" is the question you ask BECAUSE something is wrong with it.
        reviewers = []
    return WF.describe(repo, cfg, load_gates(repo, cfg), reviewers=reviewers, state=st), cfg


def show(repo: Path) -> O.Outcome:
    """The rules in force. Exit 1 when the configured workflow does not hang together.

    That exit is inherited, not chosen: an incoherent workflow — a pipeline naming an
    undefined gate, a required gate in no pipeline — is a real failure, and callers
    branch on it.
    """
    v, _cfg = _view(repo)
    data: dict[str, Any] = {
        "task_pipeline": v.task_pipeline,
        "phase_pipeline": v.phase_pipeline,
        "gates": [_plain(g) for g in v.gates],
        "rules": {k: {"value": val, "source": src} for k, (val, src) in v.rules.items()},
        "reviewers": v.reviewers,
        "overridden_prompts": v.overridden_prompts,
        "hook_installed": v.hook_installed,
        "order_violations": v.order_violations,
        "order_recordings": v.order_recordings,
        "order_violation_rate": v.order_violation_rate,
        "findings": [_plain(f) for f in v.findings],
        "coherent": not v.problems,
    }
    if v.problems:
        return O.failed("workflow.show", f"{len(v.problems)} problem(s)", **data)
    return O.ok("workflow.show", **data)


def view_for_render(repo: Path):
    """The `describe()` object itself, for the prose renderer.

    The human view needs the `WorkflowView`'s methods (`finding.render()`), not a dict of
    it, and reconstructing them from `data` would be a second representation of the same
    answer. So the surface asks for the object and asks for the Outcome, and they come
    from the same call rather than two folds — `show()` above is what defines the
    machine contract.
    """
    return _view(repo)[0]


def pipeline(repo: Path, which: str, gates: str, *, dry_run: bool = False) -> O.Outcome:
    """Set the task or phase pipeline. Refuses an empty one, and an undefined gate."""
    from ..services.gates import load_gates

    _log, cfg, _st = _load(repo)
    known = load_gates(repo, cfg)
    ids = [x.strip() for x in gates.split(",") if x.strip()]
    if not ids:
        return O.failed(
            "workflow.pipeline",
            "a pipeline with no gates is a project with no checks at all; name at least one",
            key=f"gates.{which}_pipeline",
            gates=[],
            applied=False,
        )
    unknown = [g for g in ids if g not in known]
    if unknown:
        # BEFORE writing. An unknown id in a pipeline is permanent, silent damage:
        # every item entering it blocks forever and `gate record` refuses the id.
        near = {u: [g for g in sorted(known) if g.startswith(u[:3])] for u in unknown}
        hint = "; ".join(
            f"{u!r}" + (f" (did you mean {near[u][0]!r}?)" if near[u] else "") for u in unknown
        )
        return O.failed(
            "workflow.pipeline",
            f"no gate is defined for {hint}. Every item entering this pipeline would "
            f"block on it forever. Define it first with `ddflow workflow gate <id> "
            f"--command ... | --prompt ...`, or leave it out.\nKnown: "
            f"{', '.join(sorted(known))}",
            key=f"gates.{which}_pipeline",
            gates=ids,
            applied=False,
            unknown=unknown,
        )
    key = f"gates.{which}_pipeline"
    err, _text = _write_config(repo, [(key, json.dumps(ids))], dry_run=dry_run)
    if err:
        return O.failed("workflow.pipeline", err, key=key, gates=ids, applied=False)
    return O.ok("workflow.pipeline", key=key, gates=ids, applied=not dry_run)


@dataclass
class GateEdit:
    """One gate's definition and placement, named once.

    Eleven fields, which as loose keyword arguments was both unreadable and a lint
    error. They are also the same eleven that appear as argparse flags and as MCP input
    properties, so the dataclass is the one place to add a twelfth.
    """

    id: str
    command: str = ""
    prompt: str = ""
    cwd: str = ""
    reviewer: str = ""
    title: str = ""
    timeout: int = 0
    applies_to: str = ""
    into: str = ""
    after: str = ""
    required: bool = False


def gate(repo: Path, edit: GateEdit, *, dry_run: bool = False) -> O.Outcome:
    """Define a gate, and optionally place it in a pipeline.

    Refuses to put a gate with no command and no prompt into a pipeline: that gate can
    never pass, so every item reaching it blocks forever. The check consults the
    EXISTING definition too, so `--into` on an already-defined gate is allowed.
    """
    from ..services.gates import load_gates

    _log, cfg, _st = _load(repo)
    known = load_gates(repo, cfg)
    pairs: list[tuple[str, str]] = []
    for value, field in (
        (edit.command, "command"),
        (edit.prompt, "prompt"),
        (edit.cwd, "cwd"),
        (edit.reviewer, "reviewer"),
        (edit.title, "title"),
    ):
        if value:
            pairs.append((f"gate.{edit.id}.{field}", value))
    if edit.timeout:
        pairs.append((f"gate.{edit.id}.timeout_s", str(edit.timeout)))
    if edit.applies_to:
        pairs.append((f"gate.{edit.id}.applies_to", edit.applies_to))
    if not pairs and not edit.into:
        return O.failed(
            "workflow.gate",
            "nothing to change. Give it a --command (it runs something) or a --prompt "
            "(an agent performs it and records evidence), or --into a pipeline.",
            gate=edit.id,
            changed=[],
            applied=False,
        )

    existing = known.get(edit.id)
    defines = edit.command or edit.prompt or (existing and (existing.command or existing.prompt))
    if edit.into and not defines:
        return O.failed(
            "workflow.gate",
            f"{edit.id!r} has neither a command nor a prompt, so putting it in a "
            f"pipeline would block every item that reaches it. Give it one in the same call.",
            gate=edit.id,
            changed=[],
            applied=False,
        )

    if edit.into:
        for which in ("task", "phase") if edit.into == "both" else (edit.into,):
            current = list(getattr(cfg.gates, f"{which}_pipeline"))
            if edit.id in current:
                continue
            at = len(current)
            if edit.after:
                if edit.after not in current:
                    return O.failed(
                        "workflow.gate",
                        f"--after {edit.after!r} is not in the {which} pipeline: "
                        f"{', '.join(current)}",
                        gate=edit.id,
                        changed=[],
                        applied=False,
                    )
                at = current.index(edit.after) + 1
            current.insert(at, edit.id)
            pairs.append((f"gates.{which}_pipeline", json.dumps(current)))
    if edit.required:
        pairs.append(("gates.required", json.dumps(sorted({*cfg.gates.required, edit.id}))))

    err, _text = _write_config(repo, pairs, dry_run=dry_run)
    if err:
        return O.failed(
            "workflow.gate", err, gate=edit.id, changed=[k for k, _v in pairs], applied=False
        )
    return O.ok(
        "workflow.gate",
        gate=edit.id,
        changed=[k for k, _v in pairs],
        applied=not dry_run,
    )


def drop(repo: Path, item: str, *, dry_run: bool = False) -> O.Outcome:
    """Remove a gate from both pipelines, and from `required` with it.

    Dropping it from a pipeline but leaving it `required` creates an INERT requirement:
    the rule is enforced by intersecting `required` with the pipeline, so a required
    gate in no pipeline quietly requires nothing. Its `[gate.<id>]` definition is left
    in place, because the definition is the part that is expensive to rewrite.
    """
    _log, cfg, _st = _load(repo)
    pairs: list[tuple[str, str]] = []
    removed: list[str] = []
    for which in ("task", "phase"):
        current = list(getattr(cfg.gates, f"{which}_pipeline"))
        if item in current:
            current.remove(item)
            pairs.append((f"gates.{which}_pipeline", json.dumps(current)))
            removed.append(which)
    if item in cfg.gates.required:
        pairs.append(("gates.required", json.dumps([g for g in cfg.gates.required if g != item])))
        removed.append("required")
    if not pairs:
        return O.nothing(
            "workflow.drop",
            f"{item!r} is in neither pipeline; nothing to drop",
            gate=item,
            removed_from=[],
            applied=False,
        )
    err, _text = _write_config(repo, pairs, dry_run=dry_run)
    if err:
        return O.failed("workflow.drop", err, gate=item, removed_from=removed, applied=False)
    return O.ok("workflow.drop", gate=item, removed_from=removed, applied=not dry_run)
