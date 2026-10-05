"""Onboarding, in the layer the surfaces call.

Dependency direction: surfaces -> api -> services. `onboard` dispatches by stage name,
so the CLI and the MCP tool offer the same six without either parsing the other's
output. Every acting stage PROPOSES unless `apply` says otherwise, and an unapplied
stage still returns its report -- that report is the product.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..core import outcome as O
from ..services import importer_harness as MH
from ..services import legacy as LG
from ..services import onboard as ON
from ..services import onboard_tests as OT
from ..services import onboard_verify as OV
from ._base import _load

#: The stages in the prompt's own order; the CLI choices and MCP enum come from here.
STAGES = ("status", "preflight", "legacy", "memory", "test-gate", "verify")


def preflight(repo: Path, *, apply: bool = False, only: Sequence[str] = ()) -> O.Outcome:
    """Report leftover worktrees, branches and stashes; `apply` removes the merged ones.

    The report IS the product when `apply` is false: an unmerged branch is not ours to
    delete, and the operator decides whether to land it, import it (`ddflow_import`
    proposes unmerged branches) or leave it. With `apply`, `only` is the operator's
    approval: empty removes everything the report marked `remove`; a list removes
    exactly those names (a name not marked remove is refused, never acted on). A
    removal that failed is a FAILED outcome, not a success line to parse.
    """
    leftovers = ON.preflight(repo)
    data = {"leftovers": [item.to_dict() for item in leftovers], "text": ON.render(leftovers)}
    if not leftovers:
        return O.nothing("onboard.preflight", str(data["text"]), **data)
    if not apply:
        return O.ok("onboard.preflight", **data)
    results = ON.apply(repo, list(only) if only else None)
    removed = [r for r in results if r["outcome"] == "removed"]
    refused = [r for r in results if r["outcome"] == "refused"]
    failed = [r for r in results if r["outcome"] == "failed"]
    remaining = [item.to_dict() for item in ON.preflight(repo)]
    if failed:
        return O.failed(
            "onboard.preflight",
            "; ".join(str(r.get("detail", r["name"])) for r in failed),
            **data,
            removed=removed,
            refused=refused,
            failed=failed,
            remaining=remaining,
        )
    return O.ok(
        "onboard.preflight",
        **data,
        removed=removed,
        refused=refused,
        failed=[],
        remaining=remaining,
    )


def status(repo: Path) -> O.Outcome:
    """The standing drift report: every check, none of the suite, none of the writes."""
    report = OV.verify(repo, suite=False)
    text = report.render()
    data = {
        "checks": [
            {"name": c.name, "outcome": c.outcome, "detail": c.detail} for c in report.checks
        ],
        "text": text,
    }
    if report.passed:
        return O.ok("onboard.status", **data)
    return O.failed("onboard.status", "; ".join(c.name for c in report.problems), **data)


def legacy(repo: Path, *, apply: bool = False) -> O.Outcome:
    """Rulebook cutover proposals; `apply` freezes what was imported."""
    _log, _cfg, state = _load(repo)
    imported = LG.imported_files(state)
    proposals = LG.scan(repo, imported)
    text = LG.render(proposals)
    data = {"proposals": [p.__dict__ for p in proposals], "text": text}
    if not apply:
        if not proposals:
            return O.nothing("onboard.legacy", text, **data)
        return O.ok("onboard.legacy", **data)
    actions = LG.freeze(repo, imported)
    return O.ok("onboard.legacy", **data, actions=actions)


def memory(repo: Path, *, apply: bool = False, accept: Sequence[str] = ()) -> O.Outcome:
    """Harness-memory facts: the offer; `apply` records the approved ones, once."""
    log, cfg, state = _load(repo)
    scan, kept, duplicates = MH.propose(repo, state=state, cfg=cfg)
    text = MH.render(scan, kept, duplicates)
    data = {
        "kept": [
            {"ident": f.ident, "title": f.title, "source": f.source, "body": f.body}
            for f in kept
        ],
        "duplicates": [
            {"ident": d.found.ident, "of": d.of, "identical": d.identical} for d in duplicates
        ],
        "problems": list(scan.problems),
        "text": text,
    }
    if not apply:
        if not kept and not duplicates and not scan.problems:
            return O.nothing("onboard.memory", text, **data)
        return O.ok("onboard.memory", **data)
    approved = kept
    if accept:
        wanted = set(accept)
        approved = [f for f in kept if f.ident in wanted or f.source in wanted]
    actions = MH.apply(log, approved, state=state, cfg=cfg)
    return O.ok("onboard.memory", **data, approved=[f.ident for f in approved], actions=actions)


def test_gate(repo: Path) -> O.Outcome:
    """How the project tests itself, measured in a detached clean tree; propose only."""
    report = OT.propose(repo)
    text = OT.render(report)
    data = {"text": text, "notes": list(report.notes), "known_failures": list(report.known_failures)}
    if report.runner is None:
        return O.nothing("onboard.test-gate", text, **data)
    return O.ok("onboard.test-gate", **data)


def verify(repo: Path) -> O.Outcome:
    """Every probe including the suite; a check that could not run is not a pass."""
    report = OV.verify(repo, suite=True)
    text = report.render()
    data = {
        "checks": [
            {"name": c.name, "outcome": c.outcome, "detail": c.detail} for c in report.checks
        ],
        "text": text,
    }
    if report.passed:
        return O.ok("onboard.verify", **data)
    return O.failed("onboard.verify", "; ".join(c.name for c in report.problems), **data)


def onboard(
    repo: Path,
    *,
    stage: str = "status",
    apply: bool = False,
    accept: Sequence[str] = (),
    agent: str = "",
) -> O.Outcome:
    """`stage` -> its report; unknown stages are refused with the list, never guessed."""
    if stage == "status":
        return status(repo)
    if stage == "preflight":
        return preflight(repo, apply=apply, only=accept)
    if stage == "legacy":
        return legacy(repo, apply=apply)
    if stage == "memory":
        return memory(repo, apply=apply, accept=accept)
    if stage == "test-gate":
        return test_gate(repo)
    if stage == "verify":
        return verify(repo)
    return O.refused(
        "onboard",
        f"unknown stage {stage!r}; one of: {', '.join(STAGES)}",
        text=f"unknown stage {stage!r}; one of: {', '.join(STAGES)}",
    )
