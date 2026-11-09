"""The run half of the gate pipeline: execute a command gate and record what it said.

``run`` plus the flake accounting, superseded-run and started-marker machinery around it.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core import clock
from ...core import outcome as O
from ...core import progress as PR
from ...core.model import GateOutcome
from ...infra import worktree as W
from ...services import flakes as FK
from ...services import gates as G
from ...services import leasekeep as LK
from ...services import testscope as TSC
from ...services import testselect as TS
from ...services.gates import handrecord as HR
from ...services.gates import measured as R
from ...services.gates import running as RUN
from ...services.gates import skip_followup as SF
from ...services.gates.reviewers import run_watching_git
from ...services.identity import agent_marker
from .._base import _load
from ..gate_baseline import apply as baseline_apply
from ..gate_baseline_notice import notify as notify_auto
from ..gate_guard import guard_record
from ._query import _order_gate, _recorded_since, _resolve
from ._tree import _no_tree_reason, _where_to_run, item_tree
from ._vet import (
    Evidence,
    _agent_skip_refusal,
    _docs_gate_export,
    _evidence_fields,
    _has_research_note,
    _implement_without_commits,
    _not_gated_skip_note,
    _override_gate,
    _roborev_reviewer,
    _vet_claims,
)

#: A command gate's outcome -> the exit code the caller sees. `unavailable` and `partial`
#: are 2: the gate could not report, which is not a pass and not a failure of the work.
OUTCOME_EXIT = {o.value: o.exit for o in GateOutcome}


def _lease_keeper(log, cfg, it) -> Callable[[], None] | None:
    """A callback renewing the caller's lease on `it`, or None if the caller holds none.

    A full test suite outlives a lease (25 minutes against 1800 s on the project this
    was built beside), and over MCP the caller cannot heartbeat while it is blocked in
    the call. Called between polls of the running command, on this thread -- see
    `runner.run_command_gate` for why not a background thread. See `services.leasekeep`.
    """

    return LK.keeper(log, cfg, it)


def _refuse_repeated_failure(log, cfg, st, item: str, gate: str, cwd) -> O.Outcome | None:
    """`[loops].on_detect = "block"`: a gate that has failed the same way N times is not
    run again on the work that last failed it. Changing the work lifts the refusal --
    the fingerprint is the tree's, so there is no deadlock. Warn mode never gets here."""
    if cfg.loops.on_detect != "block":
        return None

    for f in PR.detect(log.read_all(), st, cfg):
        if f.kind != "repeated_failure" or f.item != item or f.gate != gate:
            continue
        if not G.is_tree(f.tree_sha, cwd):
            return None
        return O.refused(
            "gate.run",
            f"refusing to run {gate!r} again on {item}: it has failed {f.count} times in a "
            f"row with the same output and the work has not changed since.\n"
            f"  {f.render()}\n\nChange the work first (a re-run of the same tree repeats "
            f'the failure), or set [loops].on_detect = "warn".',
            id=item,
            gate=gate,
            looping=[f.__dict__],
        )
    return None


def _fail_fast(command: str) -> bool:
    """Whether a test command stops at its first failure (`-x`, `--exitfirst`, `--maxfail`)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True  # cannot tell: do not read a missing failure line as a pass
    return any(t in ("-x", "--exitfirst") or t.startswith("--maxfail") for t in tokens)


def _note_logged(ev: dict[str, Any], n: int | None) -> None:
    """Say in the evidence how many flakes reached the log, or that it could not be written."""
    if n is None:
        ev["flakes_log_error"] = "the flake log could not be written (.ddflow/local/flakes.jsonl)"
    elif n:
        ev["flakes_logged"] = n


def _flakes_after_pass(repo, cfg, it, gate, cwd, ev) -> None:
    """A gate that PASSED on the very tree and command its previous run FAILED on: the
    tests that run named are flakes -- minus those `--rerun-failed` already logged."""
    prior = it.gates.get(gate)
    pev = prior.evidence if prior is not None and prior.outcome == "failed" else {}
    same = pev.get("tree_sha") and pev.get("tree_sha") == ev.get("tree_sha")
    if not (pev.get("failed_tests") and same and pev.get("command") == ev.get("command")):
        return
    logged = (pev.get("rerun") or {}).get("flaked", [])
    n = FK.record(
        repo, [t for t in pev["failed_tests"] if t not in logged], item=it.id, gate=gate,
        commit=W.head_sha(cwd), how="the gate re-run passed on the same tree",
        why=pev.get("failed_reasons"), tails=pev.get("failure_tails"), cfg=cfg,
    )  # fmt: skip
    _note_logged(ev, n)


def _rerun_failed_tests(repo, cfg, it, gate, gdef, cwd, ev, rerun) -> None:
    """`--rerun-failed`: run ONLY the failed tests once more; both outcomes go in ``ev``.

    The gate stays FAILED whatever the rerun says (D-failed-gate-rerun). Tests that passed
    alone are flakes -- all of them when the rerun passed, the ones it did not name when
    it failed on others (never when it stops at its first failure: the rest never ran)."""
    ids = ev.get("failed_tests") or []
    cmd = TS.run_command(gdef.command, ids, cwd) if ids else ""
    if not cmd:
        ev["rerun"] = {
            "outcome": "not run",
            "note": "no failing test id was read from the output" if not ids
            else "the gate's command does not run pytest, so the failed tests cannot be run alone",
        }  # fmt: skip
        return
    outcome, rev = rerun(replace(gdef, command=cmd))
    still = rev.get("failed_tests") or []
    flaked = ids if outcome == "passed" else [t for t in ids if t not in still] if still else []
    if outcome not in ("passed", "failed") or (outcome == "failed" and _fail_fast(gdef.command)):
        flaked = []  # only a rerun that RAN to a verdict (and was not fail-fast) proves a pass
    verdict = "all passed" if outcome == "passed" else outcome
    ev["rerun"] = {
        "command": cmd, "tests": ids, "outcome": outcome, "exit": rev.get("exit"),
        "still_failing": still, "tail": rev.get("tail", "")[-800:], "flaked": flaked,
        "note": f"{len(ids)} failed test(s) run alone once: {verdict}"
        + (f"; {len(flaked)} passed alone -- logged as flakes" if flaked else "")
        + "; the gate stays FAILED -- re-run the gate itself",
    }  # fmt: skip
    n = FK.record(
        repo, flaked, item=it.id, gate=gate, commit=W.head_sha(cwd) if cwd.exists() else "",
        how="the failed tests passed on a rerun", why=ev.get("failed_reasons"),
        tails=ev.get("failure_tails"), cfg=cfg,
    )  # fmt: skip
    _note_logged(ev, n)


def _account_flakes(repo, cfg, it, gate, gdef, cwd, result, ev, rerun, rerun_failed) -> None:
    """Flake bookkeeping for a command gate that just ran; notes it in ``ev``.

    A test that FAILED on a tree and PASSED on the same tree is a flake, and goes to the
    machine-local log (`ddflow tests --flakes`): the gate run again on identical content
    and passed, or ``rerun_failed`` ran the failed tests alone. Neither changes the gate's
    outcome: a failed gate is re-run through ddflow until it passes (D-failed-gate-rerun).
    """
    if result == "passed":
        _flakes_after_pass(repo, cfg, it, gate, cwd, ev)
    elif result == "failed" and rerun_failed:
        _rerun_failed_tests(repo, cfg, it, gate, gdef, cwd, ev, rerun)


def _failure_reason(gdef, result: str, ev: dict[str, Any]) -> str:
    """The reason a command gate's non-pass is recorded with.

    Synthesised from what actually happened. The requirement that a non-pass carries a
    reason exists so a human can act on it; for a command gate the exit code and the
    output tail ARE that reason, and demanding the caller retype them would mean the
    commonest outcome of all -- a failing suite -- could not be recorded at all."""
    reason = ev.get("reason", "")
    if reason or result == "passed":
        return reason
    tail = ev.get("tail", "").strip()
    return f"`{gdef.command}` exited {ev.get('exit')}" + (
        f": {tail.splitlines()[-1][:160]}" if tail else ""
    )


def _found_text(result: str, ev: dict[str, Any]) -> str:
    """What a finished-but-superseded run found, so its outcome is not lost (B267a6b0b5e)."""
    found = [result.upper()]
    summary = ev.get("summary") or []
    if summary and not isinstance(summary, str):
        found.append("; ".join(str(s) for s in summary))
    return " ".join(found).strip()


def _superseded_run(
    repo: Path,
    agent: str,
    item: str,
    gate: str,
    started_on: str,
    started_at: str,
    cwd: Path,
    result: str = "",
    ev: dict[str, Any] | None = None,
) -> str:
    """Why a finished run must not be recorded ("" when it may): it began on a commit that
    HEAD, or the gate's recorded outcome, has since moved past (B27a236a534). Recording it
    would put a verdict about old source over one about the new -- a stale FAILED over a
    newer PASSED, which `complete` then refuses. The refusal still says what the run found
    (its verdict and summary lines), so the outcome is not lost (B267a6b0b5e)."""
    _log, _cfg, st = _load(repo, agent)
    it = st.items.get(item)
    rec = it.gates.get(gate) if it else None
    recorded = rec.evidence.get("tree_sha", "") if rec and rec.outcome else ""
    # `merge` removes the item's worktree under a run still going; the commits live on in the
    # repository, so ancestry is asked there (git cannot answer from a missing directory, and
    # "could not tell" keeps the record: Bcf22c7f7c8).
    asked = cwd if cwd.is_dir() else repo
    found = f"; it found {_found_text(result, ev or {})}" if result else ""
    if (
        recorded
        and clock.epoch(rec.at) > clock.epoch(started_at)
        and G.moved_past(started_on, recorded, asked)
    ):
        # Recorded since this run began, on another commit (an amend or a rebase has no
        # ancestry to compare): newer work by construction.
        return (
            f"{gate} ran on {G.head_of(started_on)}, but an outcome for "
            f"{G.head_of(recorded)} was recorded while it ran: its outcome is superseded "
            f"and was not recorded{found}. Re-run `ddflow gate run {item} {gate}` on the current commit."
        )
    for newer, what in (
        (G.tree_identity(cwd), "the tree is now at"),
        (recorded, "a newer outcome is recorded on"),
    ):
        if newer and G.superseded_by(started_on, newer, asked, paths=(it.globs if it else None)):
            return (
                f"{gate} ran on {G.head_of(started_on)}, but {what} {G.head_of(newer)}: "
                f"its outcome is superseded and was not recorded{found}. Re-run `ddflow gate run "
                f"{item} {gate}` on the current commit."
            )
    return ""


def _mark_or_refuse_twin(repo, item, gate, started_on):
    """Mark, then look, under one lock: of two runs started together the first to take it
    marks and sees no twin; the second sees the first's marker and refuses.
    Returns ``(marker, None)`` to go on, or ``(None, the refusal)`` for a live twin, or
    ``(None, None)`` when no marker could be written and no twin was seen."""
    marker, twin = RUN.claim_started(repo, item, gate, started_on)
    if not twin:
        return marker, None
    return None, O.nothing(
        "gate.run",
        f"{gate} is already running on {G.head_of(started_on)} (pid {twin.pid}, since "
        f"{twin.at}); wait for it (`ddflow gate status {item}`) -- a second run would only "
        f"race it for the outcome. A run on a newer commit is allowed.",
        gate=gate,
        outcome="",
        id=item,
    )


def _merged_tree_run(repo: Path, it, cwd: Path | None):
    """A context that yields a scratch checkout of the commit ``it`` merged as, for a command
    gate whose worktree is gone and whose base stands nowhere on the merged tree (B80a4dafd02).

    `merge` removes the worktree, and once the base has moved past the merge no checkout stands
    at the merged tree, so `gate run` had nothing to run and recorded UNAVAILABLE. The merged
    work is still a committed tree at `merged_sha`, so the gate runs there. Yields None when the
    caller has a tree already, or the item is unmerged/a phase/its merged commit is not here --
    then the caller's ordinary no-tree refusal answers instead."""
    if (
        cwd is not None
        or not it.merged_sha
        or it.kind == "phase"
        or it.worktree  # a KEPT tree is a live checkout, judged by the kept-tree rules, not here
        or not W.rev(repo, it.merged_sha)
    ):
        return nullcontext(None)
    return W.scratch_tree(repo, it.merged_sha, detach=True, prefix="ddflow-merged.", quiet=True)


def _resolve_tree(repo, log, cfg, st, it, item, gate, held, cwd, made, force, gates, forced):
    """(where a command gate runs, or None; the no-tree refusal, or None).

    ``made`` is the scratch checkout `_merged_tree_run` made of the merged commit, or None;
    without one and without a live tree the gate has nowhere to run, and the no-tree reason
    is recorded instead. Moved out of `run` to keep it clear of grade-D complexity."""
    if cwd is not None:
        return cwd, None
    if made is not None and made.path is not None:
        return made.path, None
    reason, kept = _no_tree_reason(repo, it, item, gate, held, force)
    if not kept:
        R.record_measured(
            log, cfg, repo, it, gate, "unavailable", reason=reason, gates=gates, forced=forced
        )
    return None, O.nothing(
        "gate.run",
        reason,
        gate=gate,
        outcome=kept or "unavailable",
        evidence={"reason": reason},
        id=item,
    )


def run(
    repo: Path,
    item: str,
    gate: str,
    *,
    agent: str = "",
    called_from: Path | None = None,
    rerun_failed: bool = False,
    force: bool = False,
    reason: str = "",
    timeout_s: int | None = None,
) -> O.Outcome:
    """Execute a command gate and record what it said.

    ``timeout_s`` (None: as declared; else 1..max) replaces the gate's own timeout for THIS run, and the ci budget inside
    it, up to ``[gates].max_run_timeout_s``: nobody edits the shared local config to get a
    longer run on a loaded host.

    Refuses a human gate (nothing to run) and an agent gate (ddflow cannot perform it)
    with exit 2 and the instruction, rather than pretending to have run something.

    A worktree gate runs where the item's work is (`item_tree`).
    """

    resolved = _resolve(repo, item, gate, agent, timeout_s)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    ordered = _order_gate(log, cfg, st, item, gate, recording=False, defs=gates, repo=repo)
    if isinstance(ordered, O.Outcome):
        return ordered

    if gdef.is_human_gate:
        return O.nothing(
            "gate.run",
            f"gate {gate!r} is a HUMAN-APPROVAL gate. It is not something you can run or "
            f"record — it is where the operator decides whether this work should proceed, "
            f"before the compute is spent.\n\n{gdef.prompt}\n\n"
            f"Show them what you are proposing, then ask them to run:\n"
            f"  ddflow approve {item} {gate}\n"
            f"  ddflow approve {item} {gate} --reject --reason '...'\n\n"
            f"There is deliberately no MCP tool for this.",
            id=item,
            gate=gate,
            outcome="",
        )
    if not gdef.is_command_gate:
        return O.nothing(
            "gate.run",
            f"gate {gate!r} is an AGENT gate — ddflow cannot perform it.\n\n{gdef.prompt}\n\n"
            f"When done: ddflow gate record {item} {gate} --outcome passed "
            f"--evidence '<what you ran / what it said>'",
            id=item,
            gate=gate,
            outcome="",
        )

    # Before anything is written, `gate.started` and the `unavailable` below included: bob,
    # who has no tree for alice's item, wrote `unavailable` over her recorded pass (B90b43dd2d8).
    refusal, forced_by = guard_record(
        log, cfg, st, it, force=force, reason=reason, what=f"running {gate}"
    )
    if refusal:
        return O.refused("gate.run", refusal, id=item, gate=gate, outcome="")
    forced = bool(forced_by)

    cwd, held = _where_to_run(repo, cfg, st, it, gdef, called_from)
    with _merged_tree_run(repo, it, cwd) as made:
        cwd, refused = _resolve_tree(repo, log, cfg, st, it, item, gate, held, cwd, made, force, gates, forced)
        if refused is not None:
            return refused
        repeated = _refuse_repeated_failure(log, cfg, st, item, gate, cwd)
        if repeated is not None:
            return repeated
        scope, gdef, covered = _scoped(log, cfg, repo, st, it, gdef, cwd, gates, forced)
        if covered is not None:
            return covered
        started_on = G.tree_identity(cwd)
        marker, twin_refusal = _mark_or_refuse_twin(repo, item, gate, started_on)
        if twin_refusal is not None:
            return twin_refusal
        started_at = clock.now_iso()
        log.append("gate.started", item, {"gate": gate, **({"forced": True} if forced else {})})
        done = _guarded(
            log,
            cfg,
            repo,
            agent,
            it,
            item,
            gate,
            gdef=gdef,
            gates=gates,
            cwd=cwd,
            scope=scope,
            forced=forced,
            rerun_failed=rerun_failed,
            started_on=started_on,
            started_at=started_at,
            marker=marker,
        )
    if isinstance(done, O.Outcome):
        return done
    result, ev, reason = done
    data: dict[str, Any] = {"gate": gate, "outcome": result, "evidence": ev, "id": item}
    if failing := FK.failure_text(ev):
        data["failure_text"] = failing  # for the terminal; the evidence carries the same facts
    exit_code = OUTCOME_EXIT[result]
    if exit_code == O.OK:
        return O.ok("gate.run", **data)
    if exit_code == O.FAIL:
        return O.failed("gate.run", reason or f"{gate} failed", **data)
    return O.nothing("gate.run", reason or f"{gate} could not report", **data)


def _guarded(log, cfg, repo, agent, it, item, gate, **run):
    """`_execute` with the started-marker held and a SIGTERM/SIGHUP turned into a recorded
    `unavailable` (B58842554a7). ``run`` is `_execute`'s keywords."""
    started_on, started_at, gates, forced = (
        run["started_on"],
        run["started_at"],
        run["gates"],
        run["forced"],
    )
    marker = run.pop("marker")
    try:
        with RUN.terminating():
            return _execute(log, cfg, repo, agent, it, item, gate, **run)
    except RUN.RunTerminated as stop:
        reason = f"the run was terminated by {stop.signame} before its command finished"
        _log, _cfg, now = _load(repo, agent)
        rec = now.items[item].gates.get(gate) if item in now.items else None
        if _recorded_since(rec, started_at):
            # Something was recorded while the run lived: that stands, as in the sweep.
            return O.nothing("gate.run", reason, gate=gate, outcome="", evidence={}, id=item)
        R.record_measured(
            log,
            cfg,
            repo,
            it,
            gate,
            "unavailable",
            reason=reason,
            evidence={"started_tree_sha": started_on, "terminated_by": stop.signame},
            gates=gates,
            forced=forced,
        )
        return O.nothing("gate.run", reason, gate=gate, outcome="unavailable", evidence={}, id=item)
    finally:
        RUN.clear_started(marker)


def _execute(  # noqa: PLR0913 -- everything `run` resolved, handed on whole
    log,
    cfg,
    repo,
    agent,
    it,
    item,
    gate,
    *,
    gdef,
    gates,
    cwd,
    scope,
    forced,
    rerun_failed,
    started_on,
    started_at,
):
    """The started run: the command, its flake accounting and its recorded outcome.
    ``(outcome, evidence, reason)``, or the refusal when the run was superseded."""
    keeper = _lease_keeper(log, cfg, it)
    result, ev = run_watching_git(
        gdef,
        cwd,
        lambda: G.run_command_gate(
            gdef,
            cwd,
            on_tick=keeper,
            tick_s=max(1, cfg.lease.heartbeat_s) if keeper else 0,
            keep_output=G.run_log_writer(repo, item, gate),
            log_host=log.agent_id,
            cfg=cfg,
        ),
    )
    if scope is not None:
        ev.update(scope.evidence())
    ev["started_tree_sha"] = started_on
    stale = _superseded_run(repo, agent, item, gate, started_on, started_at, cwd, result, ev)
    if stale:
        return O.refused("gate.run", stale, id=item, gate=gate, outcome="", evidence=ev)

    def rerun(g, where=None):
        return G.run_command_gate(
            g,
            where or cwd,
            on_tick=keeper,
            tick_s=max(1, cfg.lease.heartbeat_s) if keeper else 0,
            cfg=cfg,
        )

    _account_flakes(repo, cfg, it, gate, gdef, cwd, result, ev, rerun, rerun_failed)
    result, red = baseline_apply(
        repo, cfg, agent, it, gate, gdef, result, ev, rerun, _fail_fast(gdef.command), cwd
    )
    reason = red or _failure_reason(gdef, result, ev)
    # Unmeasured here: `run_command_gate` measured the tree the command ran on.
    R.record_measured(
        log,
        cfg,
        repo,
        it,
        gate,
        result,
        reason=reason,
        evidence=ev,
        gates=gates,
        forced=forced,
    )
    if result == "partial" and ((ev.get("baseline") or {}).get("auto") or {}).get("accepted"):
        notify_auto(repo, agent, item, gate)
    return result, ev, reason


def _scoped(log, cfg, repo, st, it, gdef, cwd, gates, forced):
    """(the unit_tests scope or None, the gate to run, the outcome when ci covered it or None)."""
    if gdef.id != "unit_tests":
        return None, gdef, None
    scope = TSC.unit_tests_scope(cfg, st, it, gdef.command, cwd)
    if scope.scope == "covered" and forced:  # forcing a gate asks for it to run
        scope = TSC.Scope("full", "forced: this gate ran although the ci gate covered it")
    if scope.needs_ci and not forced:  # never the whole suite unasked: refuse, say rerun ci
        refusal = O.refused(
            "gate.run", scope.why, id=it.id, gate=gdef.id, outcome="", evidence=scope.evidence()
        )
        return scope, gdef, refusal
    if scope.scope == "covered":
        return scope, gdef, _covered_by_ci(log, cfg, repo, it, gdef.id, cwd, scope, gates, forced)
    return scope, replace(gdef, command=scope.command) if scope.command else gdef, None


def _covered_by_ci(log, cfg, repo, it, gate, cwd, scope, gates, forced) -> O.Outcome:
    """Record ``gate`` passed without a run: the item's ci gate took every test it would, on
    this tree (D-test-economy-r2). The evidence names the ci run and the scope."""
    ev: dict[str, Any] = {
        **scope.evidence(),
        "note": f"covered by {scope.covered_by}",
        "tree_sha": G.tree_identity(cwd),
    }
    R.record_measured(log, cfg, repo, it, gate, "passed", evidence=ev, gates=gates, forced=forced)
    return O.ok("gate.run", gate=gate, outcome="passed", evidence=ev, id=it.id)


def record(  # noqa: PLR0913 -- one keyword per facet of a gate record; all keyword-only
    repo: Path,
    item: str,
    gate: str,
    *,
    outcome: str = "",
    skip: bool = False,
    reason: str = "",
    evidence: Evidence | None = None,
    agent: str = "",
    called_from: Path | None = None,
    force: bool = False,
    approval: str = "",
) -> O.Outcome:
    """Record an outcome an agent produced, or skip the gate on the record.

    One function for both because they differ in exactly two places — the outcome is
    forced to `skipped`, and a skip records no magnitude, since nothing was reviewed and
    a diff size would imply an inspection that did not happen.
    """

    # `None`, not a shared default instance: a mutable default is one object for every
    # call, and `Evidence` is a dataclass somebody will add a list field to.
    evidence = evidence or Evidence()
    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    refused, approval_stamp = _agent_skip_refusal(
        it,
        gdef,
        gate,
        item,
        agent,
        hard_skip := skip or outcome == "skipped",
        outcome != "failed",
        G.required_gates(cfg, gates),
        gated=_override_gate(log, cfg, it, gate, item, (skip, outcome), approval, reason),
    )
    if refused is not None:
        return refused
    # B97106be46a: a refused skip already says which case it is (the refusal names the
    # request id); a skip that was NOT refused must say so too. See `_not_gated_skip_note`.

    # Recording over another clone's live lease replaces what its holder recorded (last
    # event wins): `--force` with the reason the record carries anyway (B90b43dd2d8).
    refusal, forced_by = guard_record(
        log, cfg, st, it, force=force, reason=reason, what=f"recording {gate}"
    )
    if refusal:
        return O.refused("gate.record", refusal, id=item, gate=gate, outcome="")

    ordered = _order_gate(log, cfg, st, item, gate, recording=True, defs=gates, repo=repo)
    if isinstance(ordered, O.Outcome):
        return ordered
    # The `warn` note. Carried rather than printed, so the surface decides where it goes
    # -- and so it is not silently lost, which is exactly what happened when this moved
    # out of `cmd_gate`: the api computed `ahead` and threw it away, and
    # `test_recording_a_gate_out_of_order_says_so` caught it on the full suite.
    warning = R.Order(ordered).note(gate, cfg.gates.enforce_order)
    warning = _not_gated_skip_note(
        warning,
        approval_stamp,
        it,
        gdef,
        gate,
        hard_skip,
    )

    result = "skipped" if skip else outcome
    ev, bad = _evidence_fields(evidence, item, gate, approval_stamp)
    if bad is not None:
        return bad

    warning = _docs_gate_export(
        repo,
        cfg,
        ev,
        warning,
        applies=not skip and (gate, it.kind, result) == ("docs", "phase", "passed"),
    )

    wt: Path | None = None
    if not skip:
        # WHICH tree and HOW MUCH, for AGENT gates too. These were added to
        # `run_command_gate` only, so they never reached the gates the rationale was
        # written about: "a review gate that passed over 4,000 changed lines in two
        # minutes is a different claim from one that passed over 12" -- and `rubber_duck`,
        # `critic` and `standards` are all agent gates recorded through this branch. The
        # feature missed its own motivating case.
        # Where the item's work is (B8be9373cf5): from its worktree, not the primary.
        wt, held = item_tree(repo, cfg, st, it, called_from)
        if held:
            # Recorded all the same: an agent gate is the agent's assertion, and an
            # orchestrator records gates for many items from wherever it stands. Only the
            # MEASUREMENT is ddflow's, and it is not taken from another item's tree.
            warning = " ".join(
                filter(
                    None,
                    [
                        warning,
                        f"NOTE: you are in {held}'s worktree, so nothing was measured for {item}.",
                    ],
                )
            )
        # MEASURED, and passed apart from what the caller supplied: merged into `ev`
        # they made every bare pass look evidenced (bug Bbc9a7ee3f2). Nothing, rather
        # than another item's tree, when the caller stands in one.

    # A command gate's pass is `gate run`'s to give (Bed643a611b); and what a hand record
    # could not measure is said in it (Bb305b43f56).
    why, extra = HR.hand_record(
        it, gdef, gate, result, agent_marker(agent), wt, evidence, log.agent_id, skip=skip
    )
    if why:
        return O.refused("gate.record", why, id=item, gate=gate, outcome=result)
    ev.update(extra)

    vetted = _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence, skip)
    if vetted.refusal:
        return O.refused("gate.record", vetted.refusal, id=item, gate=gate, outcome=result)
    if not force and (
        why := _implement_without_commits(
            repo, cfg, it, gate, result, wt, _has_research_note(st, it)
        )
    ):
        return O.refused("gate.record", why, id=item, gate=gate, outcome=result)
    ev.update(vetted.evidence)
    warning = " ".join(filter(None, [warning, vetted.note]))
    by, note = _roborev_reviewer(repo, wt, gate, ev, evidence.model, skip=skip, gdef=gdef)
    warning = " ".join(filter(None, [warning, note]))

    warning = " ".join(filter(None, [warning, G.untriaged_note(it, gate)]))
    try:
        R.record_measured(
            log,
            cfg,
            repo,
            it,
            gate,
            result,
            reason=reason,
            evidence=ev or None,
            gates=gates,
            by=by,
            tree=wt,
            owner_of=G.record_owner(st),
            forced=bool(forced_by),
        )
    except ValueError as exc:
        if gdef is not None and gdef.is_human_gate:
            return O.refused("gate.record", str(exc), id=item, gate=gate, outcome=result)
        return O.failed("gate.record", str(exc), id=item, gate=gate, outcome=result)
    # A SKIP files nothing about the item being settled -- the follow-up task is the log
    # entry and the reminder, and only `skip_followup = 'auto'` adds it (a skip today
    # records nothing extra). A NON-skip settles the follow-up: the gate has been
    # answered, so its re-run is no longer owed.
    warning = " ".join(
        filter(
            None, [warning, SF.record_side_effect(log, st, cfg, item, gate, result == "skipped")]
        )
    )
    return O.ok("gate.record", id=item, gate=gate, outcome=result, ahead=ordered, warning=warning)
