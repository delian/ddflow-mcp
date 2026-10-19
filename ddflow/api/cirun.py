"""`ddflow ci run`, and the `ddflow_ci` tool: run the CI parity checks and record the item's
`ci` gate from the run (a separate module from `api.ci`, which `merge` imports, because
recording goes through `api.gates`, which imports `merge`)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..core import outcome as O
from ..infra import fsio
from ..infra import worktree as W
from ..services import ci as CI
from ..services import ci_marker as CM
from ._base import _load
from .ci import lease_tick, status
from .gates import Evidence, record

#: Set by `gate run` in the environment of the command it runs: the gate that will record
#: the outcome itself, so a nested `ddflow ci run` must not record it a second time.
IN_GATE_ENV = "DDFLOW_RUNNING_GATE"


def _item_here(st, ref: str, called_from: Path | None, repo: Path) -> str:
    """The item this `ci run` is about: the item id given as the ref, else the live item
    whose worktree the caller stands in (when the ref is that tree's own HEAD), else ""."""
    if ref in st.items:
        return ref
    if called_from is None:
        return ""
    here = W.current(called_from)
    if here is None:
        return ""
    # Only a run of THIS tree's own commit is a run for its item (the CLI passes HEAD's sha).
    if ref not in ("", "HEAD") and W.rev(repo, ref) != W.rev(here.path, "HEAD"):
        return ""
    stored = W.store_path(repo, here.path)
    for it in st.items.values():
        if not it.removed and it.lease is not None and (it.worktree or "") == stored:
            return it.id
    return ""


def held_item(repo: Path, agent: str, where: Path | None) -> str:
    """The item this caller is working on when it stands in no item's tree: the one live
    lease ``agent`` holds that has a branch to test (a claim without a worktree has none),
    else "" (none, or several -- then the tree decides, as before).
    A caller standing in a tree that is an item's own keeps that tree's HEAD."""
    if not agent:
        return ""
    _log, cfg, st = _load(repo, agent)
    now = time.time()
    held = [
        it.id
        for it in st.items.values()
        if not it.removed and it.branch and it.lease is not None
        and it.lease.holder == agent and it.lease.live(now, cfg.lease.grace_s)
    ]  # fmt: skip
    if len(held) != 1:
        return ""
    if where is not None and _item_here(st, "HEAD", where, repo):
        return ""
    return held[0]


def _record_gate(repo: Path, item: str, res: CI.Result, agent: str, called_from) -> str:
    """Record the item's `ci` gate from this run, as `gate run ci` does. Returns "" when it
    was recorded, else why not (never raises: the run's own verdict stands)."""
    outcome = {"passed": "passed", "failed": "failed"}.get(res.status, "unavailable")
    note = f"ddflow ci run on {res.sha[:12] or 'the branch'}" + (
        f" merged with {res.merged_with}" if res.merged_with else ""
    )
    out = record(
        repo,
        item,
        "ci",
        outcome=outcome,
        reason="" if outcome == "passed" else res.reason or f"ci {outcome}",
        evidence=Evidence(
            note=note,
            command=res.command,
            exit_code={"passed": 0, "failed": 1}.get(res.status, 2),
            ci_scope=res.scope or None,
        ),
        agent=agent,
        called_from=called_from,
    )
    return "" if out.ok else out.reason


def run(
    repo: Path,
    *,
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    agent: str = "",
    called_from: Path | None = None,
    timeout_s: int = 0,
    log_to: Path | None = None,
    full: bool = False,
) -> O.Outcome:
    """Run the CI command on `ref` merged with `base`. 0 passed, 1 failed, 2 could not run.

    Run for an item (its id as the ref, or from the worktree of a live item) it records the
    item's `ci` gate with the commit it ran against, as `gate run <id> ci` does -- unless it
    is being run BY that gate, which records the outcome itself. ``log_to`` receives the whole
    output of the command (the integrator keeps it for a parked entry). An item's run takes
    only the tests its change reaches (`[ci].tests_scope`, D-test-economy-r2); ``full`` (the
    integrator's batch, `--full`) takes the whole suite, and so does a ref that is no item."""
    log, cfg, st = _load(repo, agent)
    if ref in ("", "HEAD") and called_from is not None and (here := W.current(called_from)):
        ref = W.rev(here.path, "HEAD") or ref  # the tree the caller stands in, not the primary's
    item = _item_here(st, ref, called_from, repo)
    if ref in st.items and st.items[ref].branch:  # an item id names its branch
        ref = st.items[ref].branch
    res = CI.run(
        repo, cfg, ref=ref, base=base, command=command, timeout_s=timeout_s,
        for_item=bool(item), full=full, tick=lease_tick(log, cfg, st, item),
    )  # fmt: skip
    data = res.as_data()
    if res.scope:
        data["scope_line"] = CM.marker_line(res.scope)
    if log_to is not None and not res.ok:  # a pass keeps no log
        log_to.parent.mkdir(parents=True, exist_ok=True)
        fsio.atomic_write(log_to, res.output, fsync=False)  # empty too: a log that says so
    if item and not command and os.environ.get(IN_GATE_ENV) != "ci":
        why = _record_gate(repo, item, res, agent, called_from)
        data["recorded"] = {
            "item": item,
            "gate": "ci",
            "ok": not why,
            **({"why": why} if why else {}),
        }
    if res.ok:
        if full:  # the periodic full-suite pass a phase close reads (schedule.phase_full_suite)
            log.append("cadence.ran", "integration_tests",
                       {"result": "0", "evidence": {"note": f"ci run --full on {ref}"}})  # fmt: skip
        return O.ok("ci.run", **data)
    if res.status == "failed":
        return O.Outcome(kind="ci.run", data=data, exit=O.FAIL, reason=res.reason)
    return O.nothing("ci.run", res.reason, **data)


def ci_tool(
    repo: Path,
    *,
    action: str = "status",
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    agent: str = "",
    called_from: Path | None = None,
    full: bool = False,
) -> O.Outcome:
    """The `ddflow_ci` tool: action run | status."""
    if action == "run":
        if ref in ("", "HEAD"):  # no ref given: the held item's branch (Be81c39acb8)
            ref = held_item(repo, agent, called_from) or ref
        return run(
            repo,
            ref=ref or "HEAD",
            base=base,
            command=command,
            agent=agent,
            called_from=called_from,
            full=full,
        )
    if action == "status":
        if base or command or (ref and ref != "HEAD"):
            return O.refused("ci", "ref, base and command are for action=run")
        return status(repo, agent=agent)
    return O.refused("ci", f"unknown action {action!r}: run | status")
