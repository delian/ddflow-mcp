"""The read half of the gate pipeline: what may run, what ran, what is next.

``status``, ``list_gates``, ``verify``, and the shared ``_resolve``/``sweep_dead_runs``
every gate operation starts from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import clock
from ...core import outcome as O
from ...core.plain import plain
from ...infra import worktree as W
from ...services import gates as G
from ...services import grants as GNT
from ...services import observed_globs as OG
from ...services import prompts as P
from ...services.approvals import overrides as OVR
from ...services.completion import changed_paths, readme_report, refine_stale_notes
from ...services.gates import measured as R
from ...services.gates import running as RUN
from ...services.gates.testcmd import coverage_of
from ...services.guidance import inject as GI
from ...views.markdown import new_reports_line
from .._base import _load
from ..lifecycle.claim import _new_report_count
from ._timeout import _with_run_timeout


def _recorded_since(rec, started_at: str) -> bool:
    """Does the gate record ``rec`` hold an outcome written at or after ``started_at``?"""
    return bool(rec and rec.outcome and clock.epoch(rec.at) >= clock.epoch(started_at))


def _partial_line(it, gate: str) -> str:
    """A partial gate's note: it RAN, so calling it "did not run" erases the part it did
    (bug B-gate-status-partial-did-not-run). The recorded coverage, else the runner's own
    counts from its summary lines, else "unknown"."""
    ev = (it.gates[gate].evidence or {}) if it.gates.get(gate) else {}
    coverage = ev.get("coverage")
    if coverage is None or coverage == "":
        coverage = coverage_of(ev)
    figure = " unknown" if coverage is None or coverage == "" else f": {coverage}"
    return f"{gate} ran only partially (coverage{figure})"


def _coverage_notes(it, s) -> list[str]:
    """The NOTE lines for the gates that did not reach a pass: the never-ran ones apart from
    the partial ones, whose coverage or counts stand (bug B-gate-status-partial-did-not-run)."""
    if not s.unavailable:
        return []
    notes = []
    never = [g for g in s.unavailable if g not in set(s.partial)]
    if never:
        notes.append(
            f"\n  NOTE: {', '.join(never)} did not run. That is a gap in coverage, not a pass."
        )
    notes.extend(f"\n  NOTE: {_partial_line(it, g)}" for g in s.partial)
    return notes


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


def _resolve(repo: Path, item: str, gate: str, agent: str, timeout_s: int | None = None):
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
    gdef = _with_run_timeout(cfg, known.get(gate), timeout_s, item, gate)
    if isinstance(gdef, O.Outcome):
        return gdef
    return log, cfg, st, it, gdef, known


def status(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """What has run, what is next, and the INSTRUCTION for the next gate.

    The instruction is the useful half — it is what tells an agent what the gate expects
    and how to record it — which is why this tool's body is prose on both surfaces.
    """

    sweep_dead_runs(repo, agent)
    _log, cfg, st = _load(repo, agent)
    gates = G.load_gates(repo, cfg)
    try:
        it = st.items[item]
    except KeyError:
        return O.failed("gate.status", f"no such item {item!r}", id=item, text="")
    stale_notes: list = []
    if it.worktree:
        cwd = W.load_path(repo, it.worktree)
        stale_notes = refine_stale_notes(
            G.stale_evidence_detail(st, cfg, item, cwd, landed="HEAD"),
            it,
            cwd,
            "HEAD",
            changed_paths(repo, it),
            cfg.review.triage_ignore_globs,
            cfg.review.doc_globs,
        )
    try:
        s = G.status(
            st, cfg, item, gates, diff=lambda: changed_paths(repo, it), stale_notes=stale_notes,
            repo=repo,
        )
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
    _lines = _coverage_notes(it, s)
    lines.extend(_lines)

    readme = readme_report(st, cfg, item, repo=repo)
    if readme:
        lines.append(f"\n  NOTE: {readme}")

    for note in _glob_notes(cfg, st, item, repo, s):
        lines.append(f"\n  NOTE: {note}")

    if granted := GNT.lines(st, item):
        lines.append("\n  " + "\n  ".join(granted))

    reports = new_reports_line(item, _new_report_count(st, item))
    if reports:
        lines.append(f"\n  NOTE: {reports}")
    return O.ok("gate.status", id=item, status=plain(s), text="\n".join(lines), readme=readme)


def _glob_notes(cfg, st, item: str, repo: Path, s) -> list[str]:
    """Declared-versus-written notices (LK-observed-globs): files written outside the
    globs, globs never written once the task is ready to merge, and the paths finished
    tasks of the same tags keep adding."""
    found = [OG.undeclared_report(st, cfg, item, repo=repo)]
    if s.current in ("merge", ""):
        found.append(OG.untouched_report(st, cfg, item, repo=repo))
    found.append(OG.learned_report(st, cfg, item, repo=repo))
    return [n for n in found if n]


def _list_overrides(log, cfg, st, narrowed: bool) -> O.Outcome:
    if narrowed:
        return O.failed("gate.list", "--overrides is its own listing; give no --refuted/--since")
    found = OVR.rows(log.read_all(), st, cfg)
    return O.ok(
        "gate.list",
        overrides=found,
        count=len(found),
        text="\n".join(OVR.line(r) for r in found) or "no gate was passed by an override",
    )


def list_gates(
    repo: Path, *, refuted: bool = False, since: str = "", agent: str = "", overrides: bool = False
) -> O.Outcome:
    """The gates this project defines; with ``refuted``, the ones recorded passed ON
    REFUTATION instead (D-unify 5), for the operator to spot-check: every (item, gate)
    whose last finding was settled by a triage rather than a clean re-review. ``since``
    (an ISO date or timestamp) keeps those recorded at or after it."""
    _log, cfg, st = _load(repo, agent)
    if overrides:
        return _list_overrides(_log, cfg, st, refuted or bool(since))
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
