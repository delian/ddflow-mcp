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
from ..services.adopt import Refused
from ..services.enforce import NEWER_HINT
from ._base import _load

#: The stages in the prompt's own order; the CLI choices and MCP enum come from here.
STAGES = ("status", "preflight", "legacy", "memory", "test-gate", "verify")


def preflight(
    repo: Path, *, apply: bool = False, only: Sequence[str] = (), agent: str = ""
) -> O.Outcome:
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
    results = ON.apply(repo, list(only) if only else None, agent=agent)
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
    if refused:
        # An approval naming nothing, or a tree someone holds, is not done: exit 3 with
        # the rows, never 0 (B32be97b560). What was removed is still reported.
        return O.refused(
            "onboard.preflight",
            "; ".join(f"{r['name']}: {r.get('detail', '')}" for r in refused),
            **data,
            removed=removed,
            refused=refused,
            failed=[],
            remaining=remaining,
        )
    return O.ok(
        "onboard.preflight",
        **data,
        removed=removed,
        refused=[],
        failed=[],
        remaining=remaining,
    )


def _verification(kind: str, *, suite: bool, repo: Path) -> O.Outcome:
    """`status` and `verify` are one report over the same checks; only whether the suite
    runs and the outcome's kind differ."""
    report = OV.verify(repo, suite=suite)
    text = report.render()
    data = {
        "checks": [
            {"name": c.name, "outcome": c.outcome, "detail": c.detail} for c in report.checks
        ],
        "text": text,
    }
    if report.passed:
        return O.ok(kind, **data)
    return O.failed(kind, "; ".join(c.name for c in report.problems), **data)


def status(repo: Path) -> O.Outcome:
    """The standing drift report: every check, none of the suite, none of the writes."""
    return _verification("onboard.status", suite=False, repo=repo)


def legacy(repo: Path, *, apply: bool = False, accept: Sequence[str] = ()) -> O.Outcome:
    """Rulebook cutover proposals; `apply` freezes the imported files the operator approved.

    The report names the freeze CANDIDATES (the files the import consumed): with no
    `accept`, apply approves that whole list; with names, only those, and a name that is
    not an imported file is refused rather than silently dropped (reviews on 2130b17).
    """
    _log, _cfg, state = _load(repo)
    imported = LG.imported_files(state)
    proposals = LG.scan(repo, imported)
    text = LG.render(proposals)
    if imported:
        text = f"{text}\nfreeze candidates ({len(imported)}): {', '.join(imported)}"
    data = {
        "proposals": [p.__dict__ for p in proposals],
        "freeze_candidates": imported,
        "text": text,
    }
    if not apply:
        if not proposals and not imported:
            return O.nothing("onboard.legacy", text, **data)
        return O.ok("onboard.legacy", **data)
    chosen, unmatched = ON.select_accepted(imported, accept, lambda n: (n,))
    refused = [
        {"name": n, "kind": "file", "outcome": "refused", "detail": "not an imported file"}
        for n in unmatched
    ]
    if not chosen:
        if not refused:
            return O.nothing("onboard.legacy", "nothing was imported to freeze", **data)
        return O.refused("onboard.legacy", "nothing approved to freeze", **data, refused=refused)
    actions = LG.freeze(repo, chosen)
    refused_actions = [str(a) for a in actions if isinstance(a, Refused)]
    if refused_actions:
        # freeze() can REFUSE to arm the ratchet (a malformed marker block, YAML that
        # will not parse, a foreign generated test). That is a failure, not a success
        # line to mix into the actions (roborev on c61278a4).
        # ... unless the refusal is a block a NEWER ddflow wrote: exit 3, upgrade (D-compat 2).
        kind = O.refused if any(NEWER_HINT in a for a in refused_actions) else O.failed
        return kind(
            "onboard.legacy", "; ".join(refused_actions), **data, actions=actions, refused=refused
        )
    return O.ok("onboard.legacy", **data, actions=actions, refused=refused)


def memory(
    repo: Path, *, apply: bool = False, accept: Sequence[str] = (), agent: str = ""
) -> O.Outcome:
    """Harness-memory facts: the offer; `apply` records the approved ones, once.

    `accept` matches a fact's ident or its source file; a name that matches nothing is
    refused and, when that leaves nothing approved, the outcome is REFUSED -- a typo
    must not read as a successful import (reviews on 2130b17).
    """
    log, cfg, state = _load(repo, agent)
    scan, kept, duplicates = MH.propose(repo, state=state, cfg=cfg)
    text = MH.render(scan, kept, duplicates)
    data = {
        "kept": [
            {"ident": f.ident, "title": f.title, "source": f.source, "body": f.body} for f in kept
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
    matched, unmatched = ON.select_accepted(kept, accept, lambda f: (f.ident, f.source))
    refused = [
        {"name": n, "kind": "fact", "outcome": "refused", "detail": "no offered fact has this name"}
        for n in unmatched
    ]
    if accept and not matched:
        return O.refused("onboard.memory", "nothing approved to record", **data, refused=refused)
    actions = MH.apply(log, matched, state=state, cfg=cfg)
    return O.ok(
        "onboard.memory",
        **data,
        approved=[f.ident for f in matched],
        actions=actions,
        refused=refused,
    )


def test_gate(repo: Path) -> O.Outcome:
    """How the project tests itself, measured in a detached clean tree; propose only."""
    report = OT.propose(repo)
    text = OT.render(report)
    data = {
        "text": text,
        "notes": list(report.notes),
        "known_failures": list(report.known_failures),
    }
    if report.runner is None:
        return O.nothing("onboard.test-gate", text, **data)
    return O.ok("onboard.test-gate", **data)


def verify(repo: Path) -> O.Outcome:
    """Every probe including the suite; a check that could not run is not a pass."""
    return _verification("onboard.verify", suite=True, repo=repo)


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
        return preflight(repo, apply=apply, only=accept, agent=agent)
    if stage == "legacy":
        return legacy(repo, apply=apply, accept=accept)
    if stage == "memory":
        return memory(repo, apply=apply, accept=accept, agent=agent)
    if stage == "test-gate":
        return test_gate(repo)
    if stage == "verify":
        return verify(repo)
    return O.refused(
        "onboard",
        f"unknown stage {stage!r}; one of: {', '.join(STAGES)}",
        text=f"unknown stage {stage!r}; one of: {', '.join(STAGES)}",
    )
