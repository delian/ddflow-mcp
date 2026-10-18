"""The gate pipeline: ask what is next, run it, record it, skip it, prove it can fail.

This family is the clearest case B36 was filed about. `cmd_gate` was 192 lines and it WAS
the rule-set: pipeline-order enforcement and its three policies, the human-gate refusal,
the agent-gate refusal, evidence assembly, the out-of-order event, and the mapping from a
gate outcome to an exit code. All of it reachable only through `main(argv)`, so none of it
was callable or testable without building an argparse Namespace.

Two rules here are worth reading before changing anything:

* **Order is enforced but not by default.** `gates.enforce_order` is `warn`, and a
  recording out of order appends a `gate.out_of_order` event either way. Reported rather
  than refused because some interleaving is legitimate, and a tool that blocks on every
  harmless reordering gets `--force`d on reflex. The event exists so that whether `warn`
  should become `block` has evidence behind it.
* **A human gate is a REFUSAL (3), never a failure (1).** Nothing went wrong; the caller
  is simply not the party who can clear it. An agent branching on exit codes needs to
  tell "this is broken" from "this is not yours to do".
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import outcome as O
from ..core import progress as PR
from ..core.model import GateOutcome
from ..core.plain import plain
from ..infra import worktree as W
from ..services import flakes as FK
from ..services import gates as G
from ..services import leasekeep as LK
from ..services import prompts as P
from ..services import roborev as RR
from ..services import testscope as TSC
from ..services import testselect as TS
from ..services.completion import changed_paths, readme_report
from ..services.export import refresh as RF
from ..services.gates import failed_gate as OC
from ..services.gates import failed_review as GO
from ..services.gates import measured as R
from ..services.gates import running as RUN
from ..services.gates.reviewers import run_watching_git
from ..services.guidance import inject as GI
from ..services.identity import agent_marker
from ..views.markdown import new_reports_line
from ._base import _load
from .gate_baseline import apply as baseline_apply
from .gate_guard import guard_record
from .lifecycle import callers_tree
from .lifecycle.claim import _new_report_count
from .lifecycle.complete import _session_model

#: A command gate's outcome -> the exit code the caller sees. `unavailable` and `partial`
#: are 2: the gate could not report, which is not a pass and not a failure of the work.
OUTCOME_EXIT = {o.value: o.exit for o in GateOutcome}


def _recorded_since(rec, started_at: str) -> bool:
    """Does the gate record ``rec`` hold an outcome written at or after ``started_at``?"""
    return bool(rec and rec.outcome and clock.epoch(rec.at) >= clock.epoch(started_at))


def sweep_dead_runs(repo: Path, agent: str, item: str = "") -> list[str]:
    """Record `unavailable` for every gate run whose process died before it recorded
    (B58842554a7): a killed shell, a tool timeout, a SIGKILL. The run's started-marker
    outlives it; a later call finds the marker with no live process and writes down what
    happened, so a started run always ends in an outcome. A gate that has an outcome since
    the run began (a newer run, a hand record) is left as it is. Returns what it swept."""
    dead = RUN.dead_runs(repo, item)
    if not dead:
        return []
    log, cfg, st = _load(repo, agent)
    known = G.load_gates(repo, cfg)
    swept = []
    for m in dead:
        it = st.items.get(m.item)
        rec = it.gates.get(m.gate) if it else None
        newer = _recorded_since(rec, m.at)
        if it is None or it.removed or it.terminal or m.gate not in known or newer:
            RUN.clear_started(m)  # nothing to record, now or ever
            continue
        reason = (
            f"the run of {m.gate} started {m.at} (pid {m.pid}) was killed before it "
            f"recorded an outcome: its process is gone. Not a pass and not a failure; "
            f"run `ddflow gate run {m.item} {m.gate}` again."
        )
        try:
            R.record_measured(
                log,
                cfg,
                repo,
                it,
                m.gate,
                "unavailable",
                reason=reason,
                evidence={"run_killed": True, "run_pid": m.pid, "run_started_at": m.at},
                gates=known,
            )
        except ValueError:
            # Refused by a rule (a human gate), which no retry changes: forget the marker
            # rather than refuse again at every call.
            RUN.clear_started(m)
            continue
        RUN.clear_started(m)
        swept.append(f"{m.item}.{m.gate}")
    return swept


def _resolve(repo: Path, item: str, gate: str, agent: str):
    """(log, cfg, state, item, gatedef) or an Outcome explaining why not.

    One place for the two existence checks every gate operation needs. `approve` once
    skipped the item check and a typo'd id MATERIALISED an item carrying a human
    approval, because the fold creates an item for an unknown subject.
    """
    sweep_dead_runs(repo, agent)
    log, cfg, st = _load(repo, agent)
    known = G.load_gates(repo, cfg)
    if gate and gate not in known:
        return O.failed(
            "gate", f"unknown gate {gate!r}; known: {', '.join(sorted(known))}", gate=gate
        )
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("gate", f"no such item {item!r}{gone}", id=item, gate=gate)
    return log, cfg, st, it, known.get(gate), known


def status(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """What has run, what is next, and the INSTRUCTION for the next gate.

    The instruction is the useful half — it is what tells an agent what the gate expects
    and how to record it — which is why this tool's body is prose on both surfaces.
    """

    sweep_dead_runs(repo, agent)
    _log, cfg, st = _load(repo, agent)
    gates = G.load_gates(repo, cfg)
    try:
        s = G.status(st, cfg, item, gates, diff=lambda: changed_paths(repo, st.items[item]))
    except KeyError:
        return O.failed("gate.status", f"no such item {item!r}", id=item, text="")

    gd = gates.get(s.current)
    lines = [f"{item}: {'COMPLETE' if s.complete else 'next = ' + (s.current or '—')}", s.render()]
    if gd:
        try:
            lines.append(
                "\n"
                + P.render(
                    P.resolve("gate_instruction", repo, P.overrides_from(cfg)), gate=gd, item=item
                )
            )
        except P.TemplateError as exc:
            # The gate is still named and still has a prompt; losing the FORMATTING of
            # the instruction must not lose the instruction.
            lines.append(f"\n{gd.title}: {gd.prompt}   [template error: {exc}]")
    if gd and not s.complete:
        governing = GI.for_item(
            cfg,
            st,
            item,
            gate=s.current,
            budget=GI.door_budget(cfg),
            heading=f"## Guidance that governs the {s.current} gate",
        )
        if governing.text:
            lines.append("\n" + governing.text.rstrip())
    if s.unavailable:
        lines.append(
            f"\n  NOTE: {', '.join(s.unavailable)} did not run. "
            f"That is a gap in coverage, not a pass."
        )

    readme = readme_report(st, cfg, item, repo=repo)
    if readme:
        lines.append(f"\n  NOTE: {readme}")

    reports = new_reports_line(item, _new_report_count(st, item))
    if reports:
        lines.append(f"\n  NOTE: {reports}")
    return O.ok("gate.status", id=item, status=plain(s), text="\n".join(lines), readme=readme)


def list_gates(repo: Path, *, refuted: bool = False, since: str = "", agent: str = "") -> O.Outcome:
    """The gates this project defines; with ``refuted``, the ones recorded passed ON
    REFUTATION instead (D-unify 5), for the operator to spot-check: every (item, gate)
    whose last finding was settled by a triage rather than a clean re-review. ``since``
    (an ISO date or timestamp) keeps those recorded at or after it."""
    _log, cfg, st = _load(repo, agent)
    if since and not refuted:
        return O.failed("gate.list", "--since narrows --refuted; give both")
    if refuted:
        rows = G.refuted_passes(st)
        if since:
            # Instants, not strings: `+02:00`, `Z` and a bare date (midnight UTC) all compare
            # as the times they are.
            try:
                floor = clock.parse_ts(since).timestamp()
            except (ValueError, TypeError):
                return O.failed("gate.list", f"--since {since!r} is not an ISO date or timestamp")
            rows = [r for r in rows if clock.epoch(r["at"], default=-1.0) >= floor]
        lines = [G.refuted_line(r) + f"  [{r['state']}] {r['title']}" for r in rows]
        return O.ok(
            "gate.list",
            refuted=True,
            count=len(rows),
            passes=rows,
            text="\n".join(lines) if lines else "no gate was passed on refutation",
        )
    known = G.load_gates(repo, cfg)
    rows = [
        {"gate": g.id, "title": g.title, "applies_to": g.applies_to, "command": g.command}
        for g in known.values()
    ]
    lines = [f"{r['gate']}  ({r['applies_to']})  {r['title']}".rstrip() for r in rows]
    return O.ok("gate.list", refuted=False, count=len(rows), gates=rows, text="\n".join(lines))


def verify(repo: Path, item: str, gate: str, *, agent: str = "") -> O.Outcome:
    """Can this gate go red at all? Applies a known-bad mutation and restores the tree.

    The only gate operation that WRITES to the working tree. Asked BEFORE the
    pipeline-order check, deliberately: this is a question about the GATE's definition,
    not about the item's progress, and with `enforce_order = "block"` it was refused for
    any gate whose predecessors had not run — which is every gate at the moment you most
    want to know the answer.
    """
    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    _log, cfg, st, it, gdef, gates = resolved

    results, reason = G.verify(st, cfg, gates, gate, repo, it)
    data: dict[str, Any] = {
        "gate": gate,
        "reason": reason,
        "results": [
            {"file": r.file, "applied": r.applied, "detected": r.detected, "detail": r.detail}
            for r in results
        ],
        # ALL THREE clauses. `results` is empty on every pre-flight failure -- unknown
        # gate, agent gate, no registered mutations, no green baseline -- and `all([])` is
        # True, so scoring on `results` alone turns each of those into a pass.
        "verified": bool(results) and not reason and all(r.ok for r in results),
    }
    # Built explicitly rather than through `O.failed(kind, reason, **data)`, because
    # `reason` is ALSO a wire field here -- `gate verify --json` has always carried the
    # pre-flight explanation under that name -- and the helper takes its own `reason`
    # positionally, so spreading `data` passed it twice and raised TypeError. Loudly, and
    # only on the failing path, which is the one nobody exercises by accident.
    if data["verified"]:
        return O.Outcome(kind="gate.verify", data=data)
    # A HUMAN gate is not unverifiable-because-broken: there is nothing a mutation could
    # demonstrate about whether a person looked. That is a coordination refusal.
    if gdef is not None and gdef.is_human_gate:
        return O.Outcome(
            kind="gate.verify",
            data=data,
            exit=O.REFUSED,
            reason=reason
            or f"{gate} is a human-approval gate; a mutation cannot prove a person looked",
        )
    return O.Outcome(
        kind="gate.verify",
        data=data,
        exit=O.FAIL,
        reason=reason
        or (
            f"{gate} did NOT catch every mutation. A gate that cannot fail is worse than "
            f"no gate — it reports success on every change and everyone downstream reads "
            f"that as evidence."
        ),
    )


def _order_gate(
    log, cfg, st, item: str, gate: str, *, recording: bool, defs=None, repo=None
) -> O.Outcome | list[str]:
    """`services.gates.measured.check_order` as an outcome: a refusal, or the gates ahead."""
    it = st.items.get(item)
    diff = (lambda: changed_paths(repo, it)) if repo is not None and it else None
    order = R.check_order(log, cfg, st, item, gate, recording=recording, defs=defs, diff=diff)
    if order.refusal:
        return O.refused("gate", order.refusal, id=item, gate=gate, ahead=order.ahead)
    return order.ahead


def _lease_keeper(log, cfg, it) -> Callable[[], None] | None:
    """A callback renewing the caller's lease on `it`, or None if the caller holds none.

    A full test suite outlives a lease (25 minutes against 1800 s on the project this
    was built beside), and over MCP the caller cannot heartbeat while it is blocked in
    the call. Called between polls of the running command, on this thread -- see
    `runner.run_command_gate` for why not a background thread. See `services.leasekeep`.
    """

    return LK.keeper(log, cfg, it)


def item_tree(repo: Path, cfg, st, it, called_from: Path | None) -> tuple[Path | None, str]:
    """(where ``it``'s work is, or None; the other item whose tree the caller is in, or "").

    Its tree. For a TASK claimed without one, the linked worktree the caller stands in
    (B8be9373cf5: run from there, a gate still ran in the primary -- `main`'s suite as
    the change's pass) -- unless that tree is another open item's -- and from the primary
    None: with worktrees on, the primary's working tree is nobody's in particular, and
    any uncommitted edit there would be credited to the item. None is recorded, not
    guessed. The primary stays the answer for a phase (its gates test the merged
    result), for an item never claimed, and with worktrees off -- the switch for a lone
    agent that works in the primary.
    """

    if it.worktree:
        return W.load_path(repo, it.worktree), ""
    if it.kind != "phase" and cfg.worktree.enabled:
        here, held = callers_tree(repo, cfg, st, it, called_from)
        if held:
            return None, held
        if here is not None:
            return here.path, ""
        if it.lease is not None:  # claimed --no-worktree, and asked from the primary
            return None, ""
    return repo, ""


def _where_to_run(repo: Path, cfg, st, it, gdef, called_from: Path | None):
    """(the directory a command gate runs in, or None; whose tree the caller is in)."""
    return (repo, "") if gdef.cwd != "worktree" else item_tree(repo, cfg, st, it, called_from)


def _no_tree_reason(it, item: str, gate: str, held: str, force: bool = False) -> tuple[str, str]:
    """(why a worktree gate has nowhere to run, the recorded outcome it leaves standing or "").
    A MERGED item's tree is gone (DX-gate-run-after-merge): a pre-merge pass is kept and
    `complete` uses it; without one the reason says to run gates before `ddflow merge`."""
    if it.merged_sha:
        sha = it.merged_sha[:12]
        prior = it.gate_outcome(gate)
        if prior == "passed" and force:
            return (
                f"{item} merged ({sha}) and the merge removed its worktree: --force asked "
                f"for a fresh {gate} measurement that cannot be taken, so the pre-merge "
                f"pass is REPLACED by unavailable (it stays in the log). Record the real "
                f"outcome with `ddflow gate record {item} {gate} --outcome passed "
                f"--evidence ...`.",
                "",
            )
        if prior == "passed":
            return (
                f"{item} merged ({sha}) and the merge removed its worktree: the recorded "
                f"{gate} outcome (passed before the merge) stands and `complete` uses it.",
                prior,
            )
        return (
            f"{item} merged ({sha}) and the merge removed its worktree, so {gate} cannot run "
            f"now; gates are run before `ddflow merge {item}`. Recording UNAVAILABLE; "
            f"if it did pass on the merged tree, say so with `ddflow gate record {item} "
            f"{gate} --outcome passed --evidence ...`.",
            "",
        )
    why = (
        f"the worktree you are in belongs to {held}, not {item}: its suite is not "
        f"{item}'s. Run the gate from the worktree {item} is worked in."
        if held
        else f"{item} was claimed without a worktree, and the primary's working tree "
        f"is not its: run the gate from the worktree it is worked in, or set "
        f"[worktree].enabled = false if the primary is where you work."
    )
    return why + " Recording UNAVAILABLE.", ""


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


def _superseded_run(
    repo: Path, agent: str, item: str, gate: str, started_on: str, started_at: str, cwd: Path
) -> str:
    """Why a finished run must not be recorded ("" when it may): it began on a commit that
    HEAD, or the gate's recorded outcome, has since moved past (B27a236a534). Recording it
    would put a verdict about old source over one about the new -- a stale FAILED over a
    newer PASSED, which `complete` then refuses."""
    _log, _cfg, st = _load(repo, agent)
    it = st.items.get(item)
    rec = it.gates.get(gate) if it else None
    recorded = rec.evidence.get("tree_sha", "") if rec and rec.outcome else ""
    # `merge` removes the item's worktree under a run still going; the commits live on in the
    # repository, so ancestry is asked there (git cannot answer from a missing directory, and
    # "could not tell" keeps the record: Bcf22c7f7c8).
    asked = cwd if cwd.is_dir() else repo
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
            f"and was not recorded. Re-run `ddflow gate run {item} {gate}` on the current commit."
        )
    for newer, what in (
        (G.tree_identity(cwd), "the tree is now at"),
        (recorded, "a newer outcome is recorded on"),
    ):
        if newer and G.superseded_by(started_on, newer, asked):
            return (
                f"{gate} ran on {G.head_of(started_on)}, but {what} {G.head_of(newer)}: "
                f"its outcome is superseded and was not recorded. Re-run `ddflow gate run "
                f"{item} {gate}` on the current commit."
            )
    return ""


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
) -> O.Outcome:
    """Execute a command gate and record what it said.

    Refuses a human gate (nothing to run) and an agent gate (ddflow cannot perform it)
    with exit 2 and the instruction, rather than pretending to have run something.

    A worktree gate runs where the item's work is (`item_tree`).
    """

    resolved = _resolve(repo, item, gate, agent)
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
    if cwd is None:
        reason, kept = _no_tree_reason(it, item, gate, held, force)
        if not kept:
            R.record_measured(
                log, cfg, repo, it, gate, "unavailable", reason=reason, gates=gates, forced=forced
            )
        return O.nothing(
            "gate.run",
            reason,
            gate=gate,
            outcome=kept or "unavailable",
            evidence={"reason": reason},
            id=item,
        )
    repeated = _refuse_repeated_failure(log, cfg, st, item, gate, cwd)
    if repeated is not None:
        return repeated
    scope, gdef, covered = _scoped(log, cfg, repo, st, it, gdef, cwd, gates, forced)
    if covered is not None:
        return covered
    started_on = G.tree_identity(cwd)
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
    marker = RUN.mark_started(repo, item, gate)
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
    stale = _superseded_run(repo, agent, item, gate, started_on, started_at, cwd)
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
        repo, cfg, agent, it, gate, gdef, result, ev, rerun, _fail_fast(gdef.command)
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
    return result, ev, reason


def _scoped(log, cfg, repo, st, it, gdef, cwd, gates, forced):
    """(the unit_tests scope or None, the gate to run, the outcome when ci covered it or None)."""
    if gdef.id != "unit_tests":
        return None, gdef, None
    scope = TSC.unit_tests_scope(cfg, st, it, gdef.command, cwd)
    if scope.scope == "covered" and forced:  # forcing a gate asks for it to run
        scope = TSC.Scope("full", "forced: this gate ran although the ci gate covered it")
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


@dataclass
class Evidence:
    """What the caller claims it did, as a record rather than five loose arguments.

    These five travel together everywhere — argparse flags, MCP input properties, the
    event payload — and grouping them is also what makes the distinction below legible:
    `note` is what a person wrote, while `tree_sha` and `diff_stat` are MEASURED here and
    deliberately not settable by the caller. A gate whose magnitude the caller supplies
    is a gate whose magnitude proves nothing.
    """

    note: str = ""
    command: str = ""
    exit_code: int | None = None
    model: str = ""
    output_file: str = ""
    #: The commit a review tool was run on (`roborev review <sha>`), checked against the
    #: item's branch head (B1ed2b4fde6).
    reviewed_sha: str = ""
    #: ``model`` was given as `--reviewer-model`: the caller states it IS the reviewer's,
    #: so an author-family name on a reviewer gate is not refused (B1979dac602).
    model_is_reviewer: bool = False
    #: Which tests a `ci run` took (`ci_scope.Plan.evidence`): recorded as `ci_scope`.
    ci_scope: dict[str, Any] | None = None


@dataclass
class _Vetted:
    refusal: str = ""
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def _implement_without_commits(repo: Path, cfg, it, gate: str, result: str, wt) -> str:
    """A refusal when `implement` is passed for an item whose own worktree has no commit
    ahead of its base, else "" (bug B31c0109012: a commit the lease hook refused was
    silenced by the driver, and the gate passed over an empty branch). An item with no
    worktree of its own, or whose tree was removed, is not judged; a tree whose range cannot
    be measured is refused, not passed."""
    if gate != "implement" or result != "passed" or not it.worktree or wt is None:
        return ""
    base = cfg.worktree.base_ref or W.default_branch(repo)
    r = W.git(wt, "rev-list", "--count", f"{base}..HEAD")
    if not (r.ok and r.out.strip() == "0"):
        return ""
    return (
        f"{it.id}'s branch has no commit ahead of {base}: there is nothing implemented to "
        f"pass. Commit the work (a commit the lease hook refused leaves nothing behind), "
        f"or --force with a --reason if the item truly changes nothing."
    )


def _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence: Evidence, skip: bool) -> _Vetted:
    """Check the caller's ``--model`` and ``--reviewed-sha`` claims against the project."""
    if skip:
        return _Vetted()
    if evidence.model and not evidence.model_is_reviewer:
        if why := _author_model_on_reviewer_gate(st, cfg, log, gate, gdef, evidence.model):
            return _Vetted(refusal=why)
    if not evidence.reviewed_sha:
        return _Vetted()
    full, why, note = _reviewed_sha_check(repo, cfg, it, wt, evidence.reviewed_sha)
    return _Vetted(refusal=why, note=note, evidence={"reviewed_sha": full} if full else {})


def _author_model_on_reviewer_gate(st, cfg, log, gate: str, gdef, model: str) -> str:
    """A refusal when ``model`` is the AUTHOR's family on a reviewer gate, else "".

    `gate record --model` names the REVIEWER's model; `complete --model` the author's.
    Recording the author's own model on a reviewer gate overwrote the reviewer's, and
    `complete` then judged independence against the author's family (B1979dac602). The
    author is the model this agent declared at `session start`; with none declared there
    is nothing to compare, and the record stands.
    """
    if not G.is_reviewer_gate(gate, gdef):
        return ""

    author = _session_model(st, log.agent_id)
    if not author:
        return ""
    fam = G.family_of(model, cfg).strip().lower()
    if not fam or fam != G.family_of(author, cfg).strip().lower():
        return ""
    return (
        f"--model {model!r} is the author's own family ({fam}, the model {log.agent_id} "
        f"declared at `session start`), and --model on a reviewer gate names the "
        f"REVIEWER's model (the author's is `complete --model`). Pass the model that "
        f"reviewed, or --reviewer-model {model} to state that this reviewer really is "
        f"of that family (it will not count as independent)."
    )


def _reviewed_sha_check(repo: Path, cfg, it, wt: Path | None, sha: str) -> tuple[str, str, str]:
    """(full sha, refusal, warning) for a ``--reviewed-sha``; the sha is "" when refused.

    `roborev review HEAD` run from an item's worktree enqueued the PRIMARY's HEAD
    (B1ed2b4fde6): the review was of main, recorded against the item. The branch head
    passes; an older commit of the item's own branch passes with a warning (it was
    reviewed, but not what will merge); anything else -- main, another branch, an
    unknown sha -- is refused.
    """

    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
        # `HEAD` resolves to a different commit in every checkout: it is the very input
        # that was wrong, so it cannot be told apart from a right one.
        return (
            "",
            (
                f"--reviewed-sha {sha!r} is not a commit sha: a symbolic ref such as HEAD "
                f"means a different commit in the primary and in a worktree. Pass the "
                f"hex sha the review ran on (`git rev-parse HEAD` in the item's worktree)."
            ),
            "",
        )
    where = wt if wt is not None and wt.exists() else repo
    full = W.rev(where, sha)
    if not full:
        return "", f"--reviewed-sha {sha!r} is not a commit in this repository.", ""
    head = W.head_sha(wt) if wt is not None and wt.exists() else ""
    if not head and it.branch:
        head = W.rev(repo, it.branch)
    if not head:
        return full, "", "NOTE: no branch head to compare --reviewed-sha with; recorded as given."
    if full == head:
        return full, "", ""
    base = cfg.worktree.base_ref or W.default_branch(repo)
    own = W.git(where, "rev-list", f"{base}..{head}")
    if own.ok and full in own.out.split():
        return (
            full,
            "",
            (
                f"NOTE: --reviewed-sha {full[:10]} is an older commit of the branch; its head "
                f"is {head[:10]}. What merges is not what was reviewed."
            ),
        )
    return (
        "",
        (
            f"--reviewed-sha {full[:10]} is not the item's branch head ({head[:10]}) nor a "
            f"commit of its branch: the review was of another commit (`roborev review HEAD` "
            f"from a worktree can enqueue the primary's HEAD). Re-run it with the explicit "
            f"sha: `roborev review {head[:10]}`."
        ),
        "",
    )


def _roborev_reviewer(
    repo: Path,
    wt: Path | None,
    gate: str,
    ev: dict[str, Any],
    typed: str,
    *,
    skip: bool,
    gdef=None,
) -> tuple[str, str]:
    """(the reviewer to record, a note) for a reviewer gate recorded with --reviewed-sha.

    The agent roborev ENQUEUED a job for is not always the one that ran it: when it
    fails, the daemon falls back to its backup agent -- kilo enqueued, claude-code
    reviewed (B03437a6b45). The model the caller typed was recorded either way, so a
    same-family reviewer passed as cross-family. roborev's job record names the agent
    that ran; that is recorded as the reviewer (in ``ev``, as `--reviewer-model` would:
    a same-family one is then flagged at completion, not refused here). Without roborev,
    or without a finished review of the sha, the typed model stands, with a note and
    ``evidence.roborev.verified = false``.
    """

    if skip or not G.is_reviewer_gate(gate, gdef) or not ev.get("reviewed_sha"):
        return typed, ""
    where = wt if wt is not None and wt.exists() else repo
    rv, note = RR.review_of(where, str(ev["reviewed_sha"]))
    if rv is None or not rv.reviewer:
        # Nobody vouched for the typed model: said in the record, not only on screen.
        why = (
            f"roborev job {rv.job} names no agent"
            if rv is not None
            else note or "roborev gave no answer"
        )
        ev["roborev"] = {"verified": False, "why": why}
        return typed, f"NOTE: {why}."
    ev["roborev"] = {"verified": True, "job": rv.job, "agent": rv.agent, "model": rv.model}
    ev["model"] = rv.reviewer
    if not typed or typed in (rv.agent, rv.model):
        return rv.reviewer, ""
    ran = rv.agent + (f" ({rv.model})" if rv.model else "")
    return rv.reviewer, (
        f"NOTE: roborev job {rv.job} was reviewed by {ran}, not {typed!r} (its backup "
        f"agent ran when the requested one failed?): {rv.reviewer!r} is recorded as the "
        f"reviewer."
    )


def _docs_gate_export(
    repo: Path, cfg, ev: dict[str, Any], warning: str, *, applies: bool = True
) -> str:
    """`[export].refresh = docs_gate`: the docs gate's export step. Regenerates and verifies
    the selected documents and records them with their body digests in ``ev``; returns the
    warning, extended when a document was skipped, failed or is not fresh. Never fails."""
    if not applies:
        return warning

    rr = RF.refresh_selected(repo, "docs_gate", cfg=cfg)
    if not rr.outcomes:
        return " ".join(filter(None, [warning, f"NOTE: {rr.note}" if rr.note else ""]))
    ev["export"] = rr.evidence()
    if rr.problems or any(o.verified is False for o in rr.outcomes):
        return " ".join(filter(None, [warning, f"NOTE: {rr.summary()}"]))
    return warning


def _evidence_fields(
    evidence: Evidence, item: str, gate: str
) -> tuple[dict[str, Any], O.Outcome | None]:
    """The event's evidence from what the caller supplied, or the failure (an unreadable
    ``--output-file``)."""
    ev: dict[str, Any] = {}
    if evidence.note:
        ev["note"] = evidence.note
    if evidence.command:
        ev["command"] = evidence.command
    if evidence.exit_code is not None:
        ev["exit"] = evidence.exit_code
    if evidence.model:
        ev["model"] = evidence.model
    if evidence.ci_scope:
        ev["ci_scope"] = evidence.ci_scope
    if evidence.output_file:
        try:
            txt = Path(evidence.output_file).read_text("utf-8", errors="replace")
        except OSError as exc:
            return ev, O.failed(
                "gate.record", f"--output-file unreadable: {exc}", id=item, gate=gate
            )
        ev.update(G.output_evidence(txt))
        # WHERE the digested output is, so the digest can be checked against it.
        ev["output_file"] = evidence.output_file
    return ev, None


def _agent_skip_refusal(
    it, gdef, gate: str, item: str, agent: str, hard_skip: bool, hand: bool, required=()
) -> O.Outcome | None:
    """D-failed-review-gate (B85841c0846): replacing the FAILED outcome of a review gate whose
    confirmed findings were never re-reviewed -- by a skip, or a hand-recorded pass,
    unavailable or partial -- is the operator's override. An agent (`--agent`, DDFLOW_AGENT,
    a harness's shell) is refused; the person records the reason. A review that really runs
    (`ddflow review`, a triage) does not come through here.

    DX-skip-failed-gate: a skip of any other REQUIRED gate that is FAILED is refused for
    everyone (`skip_over_failure_refusal`); a failure is recorded as one, or as unavailable."""
    if hard_skip and not (gdef and gdef.reviewer):
        if why := OC.skip_over_failure_refusal(it, gate, item, required):
            return O.refused("gate.record", why, id=item, gate=gate, outcome="")
    if not (hard_skip or hand) or gdef is None or not gdef.reviewer:
        return None
    why = GO.unresolved_review_failure(it, gate, confirmed_only=not hard_skip)
    marker = agent_marker(agent) if why else ""
    if not marker:
        return None
    return O.refused(
        "gate.record",
        f"{gate} failed ({why}); setting it aside past those findings is the operator's "
        f"override, and this call is an agent's ({marker}). Fix and re-run "
        f"`ddflow review {item} --gate {gate}`, or ask the operator to run "
        f"`ddflow gate skip {item} {gate} --reason ...` from their own terminal.",
        id=item,
        gate=gate,
        outcome="",
    )


def record(
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

    refused = _agent_skip_refusal(
        it,
        gdef,
        gate,
        item,
        agent,
        skip or outcome == "skipped",
        outcome != "failed",
        G.required_gates(cfg, gates),
    )
    if refused is not None:
        return refused

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

    result = "skipped" if skip else outcome
    ev, bad = _evidence_fields(evidence, item, gate)
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

    vetted = _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence, skip)
    if vetted.refusal:
        return O.refused("gate.record", vetted.refusal, id=item, gate=gate, outcome=result)
    if not force and (why := _implement_without_commits(repo, cfg, it, gate, result, wt)):
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
    return O.ok("gate.record", id=item, gate=gate, outcome=result, ahead=ordered, warning=warning)
