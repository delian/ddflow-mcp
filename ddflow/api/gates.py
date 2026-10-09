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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import outcome as O
from ..core.model import GateOutcome
from ..core.plain import plain
from ..services import gates as G
from ..services import testselect as TS
from ..services.gates.reviewers import run_watching_git
from ._base import _load

#: A command gate's outcome -> the exit code the caller sees. `unavailable` and `partial`
#: are 2: the gate could not report, which is not a pass and not a failure of the work.
OUTCOME_EXIT = {o.value: o.exit for o in GateOutcome}


def _gates_ahead_of(st, cfg, item_id: str, gate: str) -> list[str]:
    """Pipeline gates BEFORE ``gate`` that have no outcome yet.

    The order in `gates.task_pipeline` is not decoration: a rubber-duck review recorded
    before `implement` reviewed an empty diff, and a `merge` recorded before `unit_tests`
    merged something nobody tested.
    """
    try:
        s = G.status(st, cfg, item_id)
    except KeyError:
        return []
    if gate not in s.pipeline:
        return []
    before = s.pipeline[: s.pipeline.index(gate)]
    it = st.items.get(item_id)
    return [g for g in before if it and not it.gate_outcome(g)]


def _order_note(ahead: list[str], gate: str) -> str:
    return (
        f"{gate} comes after {', '.join(ahead)} in the pipeline, and "
        f"{'none of those have' if len(ahead) > 1 else 'that one has not'} run yet."
    )


def _resolve(repo: Path, item: str, gate: str, agent: str):
    """(log, cfg, state, item, gatedef) or an Outcome explaining why not.

    One place for the two existence checks every gate operation needs. `approve` once
    skipped the item check and a typo'd id MATERIALISED an item carrying a human
    approval, because the fold creates an item for an unknown subject.
    """
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
    from ..services import prompts as P

    _log, cfg, st = _load(repo, agent)
    try:
        s = G.status(st, cfg, item)
    except KeyError:
        return O.failed("gate.status", f"no such item {item!r}", id=item, text="")

    gates = G.load_gates(repo, cfg)
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
    if s.unavailable:
        lines.append(
            f"\n  NOTE: {', '.join(s.unavailable)} did not run. "
            f"That is a gap in coverage, not a pass."
        )
    from ..services.completion import readme_report

    readme = readme_report(st, cfg, item, repo=repo)
    if readme:
        lines.append(f"\n  NOTE: {readme}")
    from ..views.markdown import new_reports_line
    from .lifecycle import _new_report_count

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


def _order_gate(log, cfg, st, item: str, gate: str, *, recording: bool) -> O.Outcome | list[str]:
    """Enforce `gates.enforce_order`, and RECORD the violation either way."""
    ahead = _gates_ahead_of(st, cfg, item, gate)
    if not ahead or cfg.gates.enforce_order == "off":
        return []
    if cfg.gates.enforce_order == "block":
        return O.refused(
            "gate",
            f"{_order_note(ahead, gate)}\nThe order is the point: reviewing a change "
            f"before it is implemented reviews nothing. Run them in order, or set "
            f"[gates].enforce_order = 'warn'.",
            id=item,
            gate=gate,
            ahead=ahead,
        )
    if recording:
        # Recorded, not just printed. Whether "warn" should become "block" or "off" is a
        # judgement about how often this fires, and for as long as it only ever printed,
        # that judgement had no evidence behind it either way.
        log.append(
            "gate.out_of_order",
            item,
            {"gate": gate, "ahead": ahead, "policy": cfg.gates.enforce_order},
        )
    return ahead


def _lease_keeper(log, cfg, it) -> Callable[[], None] | None:
    """A callback renewing the caller's lease on `it`, or None if the caller holds none.

    A full test suite outlives a lease (25 minutes against 1800 s on the project this
    was built beside), and over MCP the caller cannot heartbeat while it is blocked in
    the call. Called between polls of the running command, on this thread -- see
    `runner.run_command_gate` for why not a background thread. A failed renewal must not kill
    the gate, so it is swallowed; the lease then expires as it would have anyway.
    """
    from ..services import leases as L

    if not (it.lease and it.lease.holder == log.agent_id):
        return None
    holder = it.lease.holder

    def renew() -> None:
        try:
            L.renew(log, it.id, holder)
        except Exception:
            pass

    return renew


def _item_tree(repo: Path, cfg, st, it, called_from: Path | None) -> tuple[Path | None, str]:
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
    from ..infra import worktree as W
    from .lifecycle import callers_tree

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


def _measure(repo: Path, it, wt: Path | None) -> dict:
    """What ddflow measures for a recorded gate: the item's tree. Once the item has
    landed and its tree was kept, a tree that differs from what landed ONLY by untracked
    files (scratch that never landed) is measured as what landed: those files made every
    gate recorded after the merge read as stale at complete (Bb47a48b173). Any other
    difference -- work committed or edited after the landing -- is kept, so complete
    still reports it."""
    if not wt:
        return {}
    measured = {
        "tree_sha": G.tree_fingerprint(wt),
        "source_tree": G.source_tree(wt),
        "diff_stat": G.diff_stat(wt),
    }
    measured.update(_landed_if_only_untracked_differs(repo, it, wt, measured["source_tree"]))
    return measured


def _landed_if_only_untracked_differs(repo: Path, it, wt: Path, here: str) -> dict:
    """`tree_sha`/`source_tree` of the landed commit when ``wt`` holds exactly that content
    plus untracked files; {} otherwise (not landed, the same already, or a real change)."""
    from ..services.completion import _tree_being_completed

    if not (it.landed_after or it.merged_sha):
        return {}
    _cwd, landed = _tree_being_completed(repo, it)
    source = G.commit_source_tree(repo, landed) if landed else ""
    if not source or source == here:
        return {}
    entries = G.worktree_entries(wt)
    if entries is None:
        return {}
    untracked = set(G._untracked_paths(wt))
    tracked_only = {p: e for p, e in entries.items() if p not in untracked}
    if G.content_id(tracked_only) != source:
        return {}  # the tree changed beyond scratch: let complete say so
    return {"tree_sha": f"{landed[:12]}+clean", "source_tree": source}


def _where_to_run(repo: Path, cfg, st, it, gdef, called_from: Path | None):
    """(the directory a command gate runs in, or None; whose tree the caller is in)."""
    return (repo, "") if gdef.cwd != "worktree" else _item_tree(repo, cfg, st, it, called_from)


def _refuse_repeated_failure(log, cfg, st, item: str, gate: str, cwd) -> O.Outcome | None:
    """`[loops].on_detect = "block"`: a gate that has failed the same way N times is not
    run again on the work that last failed it. Changing the work lifts the refusal --
    the fingerprint is the tree's, so there is no deadlock. Warn mode never gets here."""
    if cfg.loops.on_detect != "block":
        return None
    from ..core import progress as PR

    for f in PR.detect(log.read_all(), st, cfg):
        if f.kind != "repeated_failure" or f.item != item or f.gate != gate:
            continue
        if not f.tree_sha or G.tree_fingerprint(cwd) != f.tree_sha:
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


def run(
    repo: Path, item: str, gate: str, *, agent: str = "", called_from: Path | None = None
) -> O.Outcome:
    """Execute a command gate and record what it said.

    Refuses a human gate (nothing to run) and an agent gate (ddflow cannot perform it)
    with exit 2 and the instruction, rather than pretending to have run something.

    A worktree gate runs where the item's work is (`_item_tree`).
    """

    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    ordered = _order_gate(log, cfg, st, item, gate, recording=False)
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

    cwd, held = _where_to_run(repo, cfg, st, it, gdef, called_from)
    if cwd is None:
        reason = (
            f"the worktree you are in belongs to {held}, not {item}: its suite is not "
            f"{item}'s. Run the gate from the worktree {item} is worked in."
            if held
            else f"{item} was claimed without a worktree, and the primary's working tree "
            f"is not its: run the gate from the worktree it is worked in, or set "
            f"[worktree].enabled = false if the primary is where you work."
        ) + " Recording UNAVAILABLE."
        G.record(log, cfg, item, gate, "unavailable", reason=reason, gates=gates)
        return O.nothing("gate.run", reason, gate=gate, outcome="unavailable", evidence={}, id=item)
    repeated = _refuse_repeated_failure(log, cfg, st, item, gate, cwd)
    if repeated is not None:
        return repeated
    scope = TS.unit_tests_scope(cfg, st, it, gdef.command, cwd) if gate == "unit_tests" else None
    if scope is not None and scope.command:
        gdef = replace(gdef, command=scope.command)
    log.append("gate.started", item, {"gate": gate})
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
        ),
    )
    if scope is not None:
        ev.update(scope.evidence())
    reason = ev.get("reason", "")
    if not reason and result != "passed":
        # Synthesised from what actually happened. The requirement that a non-pass carries
        # a reason exists so a human can act on it; for a command gate the exit code and
        # the output tail ARE that reason, and demanding the caller retype them would mean
        # the commonest outcome of all -- a failing suite -- could not be recorded at all.
        tail = ev.get("tail", "").strip()
        reason = f"`{gdef.command}` exited {ev.get('exit')}" + (
            f": {tail.splitlines()[-1][:160]}" if tail else ""
        )
    G.record(log, cfg, item, gate, result, reason=reason, evidence=ev, gates=gates)
    data: dict[str, Any] = {"gate": gate, "outcome": result, "evidence": ev, "id": item}
    exit_code = OUTCOME_EXIT[result]
    if exit_code == O.OK:
        return O.ok("gate.run", **data)
    if exit_code == O.FAIL:
        return O.failed("gate.run", reason or f"{gate} failed", **data)
    return O.nothing("gate.run", reason or f"{gate} could not report", **data)


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


@dataclass
class _Vetted:
    refusal: str = ""
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


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
    from .lifecycle import _session_model

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
    from ..infra import worktree as W

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
    from ..services import roborev as RR

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


def _docs_gate_export(repo: Path, cfg, ev: dict[str, Any], warning: str) -> str:
    """`[export].refresh = docs_gate`: the docs gate's export step. Regenerates and verifies
    the selected documents and records them with their body digests in ``ev``; returns the
    warning, extended when a document was skipped, failed or is not fresh. Never fails."""
    from ..services.export import refresh as RF

    rr = RF.refresh_selected(repo, "docs_gate", cfg=cfg)
    if not rr.outcomes:
        return " ".join(filter(None, [warning, f"NOTE: {rr.note}" if rr.note else ""]))
    ev["export"] = rr.evidence()
    if rr.problems or any(o.verified is False for o in rr.outcomes):
        return " ".join(filter(None, [warning, f"NOTE: {rr.summary()}"]))
    return warning


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

    ordered = _order_gate(log, cfg, st, item, gate, recording=True)
    if isinstance(ordered, O.Outcome):
        return ordered
    # The `warn` note. Carried rather than printed, so the surface decides where it goes
    # -- and so it is not silently lost, which is exactly what happened when this moved
    # out of `cmd_gate`: the api computed `ahead` and threw it away, and
    # `test_recording_a_gate_out_of_order_says_so` caught it on the full suite.
    warning = (
        f"NOTE: {_order_note(ordered, gate)} Recording anyway "
        f"([gates].enforce_order = '{cfg.gates.enforce_order}')."
        if ordered
        else ""
    )

    result = "skipped" if skip else outcome
    ev: dict[str, Any] = {}
    if evidence.note:
        ev["note"] = evidence.note
    if evidence.command:
        ev["command"] = evidence.command
    if evidence.exit_code is not None:
        ev["exit"] = evidence.exit_code
    if evidence.model:
        ev["model"] = evidence.model
    if evidence.output_file:
        try:
            txt = Path(evidence.output_file).read_text("utf-8", errors="replace")
        except OSError as exc:
            return O.failed("gate.record", f"--output-file unreadable: {exc}", id=item, gate=gate)
        ev.update(G.output_evidence(txt))
        # WHERE the digested output is, so the digest can be checked against it.
        ev["output_file"] = evidence.output_file

    if not skip and gate == "docs" and it.kind == "phase" and result == "passed":
        warning = _docs_gate_export(repo, cfg, ev, warning)

    wt: Path | None = None
    if not skip:
        # WHICH tree and HOW MUCH, for AGENT gates too. These were added to
        # `run_command_gate` only, so they never reached the gates the rationale was
        # written about: "a review gate that passed over 4,000 changed lines in two
        # minutes is a different claim from one that passed over 12" -- and `rubber_duck`,
        # `critic` and `standards` are all agent gates recorded through this branch. The
        # feature missed its own motivating case.
        # Where the item's work is (B8be9373cf5): from its worktree, not the primary.
        wt, held = _item_tree(repo, cfg, st, it, called_from)
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
        measured = _measure(repo, it, wt)
    else:
        measured = {}

    vetted = _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence, skip)
    if vetted.refusal:
        return O.refused("gate.record", vetted.refusal, id=item, gate=gate, outcome=result)
    ev.update(vetted.evidence)
    warning = " ".join(filter(None, [warning, vetted.note]))
    by, note = _roborev_reviewer(repo, wt, gate, ev, evidence.model, skip=skip, gdef=gdef)
    warning = " ".join(filter(None, [warning, note]))

    try:
        G.record(
            log,
            cfg,
            item,
            gate,
            result,
            reason=reason,
            evidence=ev or None,
            gates=gates,
            by=by,
            measured=measured,
        )
    except ValueError as exc:
        if gdef is not None and gdef.is_human_gate:
            return O.refused("gate.record", str(exc), id=item, gate=gate, outcome=result)
        return O.failed("gate.record", str(exc), id=item, gate=gate, outcome=result)
    return O.ok("gate.record", id=item, gate=gate, outcome=result, ahead=ordered, warning=warning)
