"""Health checks: `ddflow doctor` and every finding it assembles, and `ddflow recover`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import outcome as O
from ...core import progress as PR
from ...core.model import ABANDONED, DONE
from ...core.plain import plain
from ...core.schedule import is_external, stale_package_globs, unpickable
from ...core.tier import unknown_tier_notes
from ...infra import container as CT
from ...infra import signals as SIG
from ...infra import worktree as W
from ...infra.store import Store
from ...infra.worktree import absolutise
from ...services import adopt as AD
from ...services import cleanup as CL
from ...services import configcompat as CC
from ...services import embed as EMB
from ...services import eventcommit as EC
from ...services import external as EX
from ...services import flowstate as FL
from ...services import launchers as LA
from ...services import leases as L
from ...services import rates as RT
from ...services import repairs as RP
from ...services import sessions as SS
from ...services import shared_files as SF
from ...services import upgrade as UP
from ...services import workflow as WF
from ...services.adopt import MISSING, NOT_BINDING, rules_status
from ...services.completion import verdict
from ...services.export import ops as X
from ...services.export import select as export_select
from ...services.export.query import ExportError
from ...services.gates import load_gates
from ...services.guidance import ruleview as RULEVIEW
from ...services.review import load_reviewers
from ...views import human
from ...views.markdown import may_hold_work
from .._base import _load
from ..knowledge import _sweep_records, pairs_from
from ..lifecycle.planning import plan_for
from ..refs import stale_references


def recover(repo: Path, *, item: str = "", apply: bool = False, agent: str = "") -> O.Outcome:
    """Crashed agents' leases and orphaned worktrees. Exit 2 when there is nothing.

    A worktree with uncommitted changes is reported and never touched, whatever `apply`
    says — the whole value of the sweep is that it does not destroy the work it found.
    """

    log, cfg, _ = _load(repo, agent)
    found = [r for r in L.sweep(log, cfg, repo, apply=apply) if not item or r.item == item]
    # Every entry that may hold work, as the brief bands them: a tree git could not
    # measure and a RUNNING item nobody holds count too (B3f8c406fea, Be0d308babd).
    salvageable = [r for r in found if may_hold_work(r)]
    data: dict[str, Any] = {
        "found": [absolutise(repo, plain(r)) for r in found],
        "count": len(found),
        "salvageable": len(salvageable),
        "applied": apply,
        "_render": {"found": found, "salvageable": salvageable},
    }
    if not found:
        return O.nothing(
            "recover", "Nothing to recover — no expired leases, no orphan worktrees.", **data
        )
    return O.ok("recover", **data)


def _dependency_findings(repo: Path, cfg, st, problems: list[str], notes: list[str]) -> None:
    """Dependencies that can never be met, and external ones not yet observed."""

    configured = EX.repos(cfg, repo)
    # Live items only, as `external.referenced` observes: a removed item's dependency
    # produced a finding `external sync` would never clear (roborev 829).
    for it in (i for i in st.items.values() if not i.removed):
        problems += [
            f"{it.id} needs unknown item {dep!r}"
            for dep in it.needs
            if dep not in st.items and not is_external(dep)
        ]
        for dep in (d for d in it.needs if is_external(d)):
            name = dep.partition(":")[0]
            if name not in configured:
                problems.append(
                    f"{it.id} needs {dep!r}, but {name!r} is not in [schedule] repos -- it "
                    f"can never be observed, so {it.id} can never start"
                )
            elif dep not in st.external:
                notes.append(f"{it.id} needs {dep!r}, not yet observed: `ddflow external sync`")


def _stale_glob_notes(repo: Path, st) -> list[str]:
    """Unfinished items whose `x.py` glob names a module that is now the package `x/`: a
    lease on it covers none of the package's files, so overlaps go unseen (B56dc2baaf6).
    Each is named with the corrected glob and the command that applies it."""
    live = [i for i in st.items.values() if not i.removed and i.state not in (DONE, ABANDONED)]
    if not any(g.endswith(".py") for i in live for g in i.globs):
        return []
    tracked = W.git_paths(repo, "ls-files")
    if tracked is None:  # "could not tell" is said, never read as "nothing stale"
        return ["stale task globs not checked: git could not list the tracked files"]
    notes = []
    for it in live:
        stale = stale_package_globs(it.globs, tracked)
        if not stale:
            continue
        fixed = dict(stale)
        globs = ",".join(fixed.get(g, g) for g in it.globs)
        notes.append(
            f"{it.id} declares {', '.join(f'{o} (now the package {n})' for o, n in stale)}: "
            f"its lease covers none of those files. `ddflow update {it.id} --globs {globs}`"
        )
    return notes


def _finished_phase_remedy(detail: str, st, cfg: Config, item: str, repo: Path) -> str:
    """B5189cc5756: offer `ddflow complete` only when it would succeed.

    `unpickable()` (core) states the fact and the default remedy; whether completion is
    allowed is decided in exactly one place, `completion.verdict()`, so it is asked here
    rather than re-derived. `model=""` is harmless: the reviewer-independence blocker is
    applied to tasks only, never to a phase, so an unknown author model cannot put a
    spurious blocker into this remedy.
    """

    v = verdict(st, cfg, item, repo=repo)
    if v.may_complete:
        return detail
    fact = detail.split(" — ", 1)[0]
    # Quoted whole, not cut at a first ". ": that split is not sentence-aware, and a gate
    # named `review. final` came out as `review`, a gate that does not exist.
    why = " ".join(b.strip() for b in v.blockers)
    return (
        f"{fact} — `ddflow complete {item}` would refuse: {why} See "
        f"`ddflow gate status {item}`, or file the work that remains"
    )


def _primary_mid_merge(repo: Path, problems: list[str], notes: list[str]) -> None:
    """B6926ec1ad9: a merge left half-done in the primary fails every later `merge`, by
    every agent, and agents may not touch the primary to clear it."""

    mid = W.merging(repo)
    if mid is None:
        notes.append(f"git could not list unmerged paths in {repo}: mid-merge state unknown")
        return
    if not mid:
        return
    in_merge = W.git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").ok
    problems.append(
        f"the primary checkout {repo} is mid-merge "
        + (
            "(MERGE_HEAD set): every `ddflow merge` will fail until it is concluded "
            f"or aborted. If nobody is resolving it by hand, `git -C {repo} merge --abort`."
            if in_merge
            else "(unmerged paths, no MERGE_HEAD: a squash, cherry-pick or rebase left "
            f"half-done): every `ddflow merge` will fail. `git -C {repo} status` names the "
            f"operation and its --abort; a squash is cleared with `git -C {repo} reset "
            "--merge`."
        )
    )


#: How many new shard names the doctor note lists before it counts the rest.
_SHARDS_SHOWN = 8


def _unknown_author_notes(repo: Path, log, ctx: RP.Context | None = None) -> list[str]:
    """ONE note naming event shards whose agent id has no committed history.

    A pull request can add `.ddflow/events/<anybody>.jsonl`: the log is merged by union
    with content-addressed ids and no signatures, so a new author's first records arrive
    with the standing of everyone else's (`core/provenance.py` marks what they say; this
    says that a stranger said it). "Seen before" is the base branch's committed tree --
    this agent's own shard is never new to itself -- and a shard the operator reviewed
    (data repair `unknown-author-shards`) is not named again. Git unable to say is
    `unavailable`, never silence: a repository where the check could not run must not
    read as clean.
    """
    ctx = ctx or RP.context(repo, log, Config.load(repo))
    try:
        new, base = RP.unknown_authors(ctx)
    except RP.Unavailable as exc:
        return [
            f"unavailable: event-shard authorship was not checked ({exc}) -- git could not "
            "say which agent ids are new, so none are reported as known"
        ]
    if not new:
        return []
    names = ", ".join(n.removesuffix(".jsonl") for n in new[:_SHARDS_SHOWN])
    names += " ..." if len(new) > _SHARDS_SHOWN else ""
    return [
        f"{len(new)} event shard(s) written by an agent id with no committed history on "
        f"{base}: {names} -- a pull request can add one. Check who wrote it "
        "(`git log -- .ddflow/events/<id>.jsonl`) before trusting its decisions and lessons"
    ]


def _driver_drift_notes(repo: Path) -> list[str]:
    """One note naming driver docs that differ from the templates this ddflow ships, each
    with WHAT the difference is (the Managed state): an unedited older release, a hand edit,
    a newer ddflow's format, or a copy with no stamp."""

    lagging = AD.driver_states(repo)
    if not lagging:
        return []
    why = {
        AD.DOC_STALE: "older release",
        AD.DOC_EDITED: "edited by hand",
        AD.DOC_NEWER: "newer format: upgrade ddflow",
        AD.DOC_LEGACY: "no version stamp",
    }
    names = ", ".join(f"{rel} ({why.get(st, st)})" for rel, st in lagging.items())
    note = f"driver docs differ from the templates this ddflow ships: {names}"
    if all(st == AD.DOC_NEWER for st in lagging.values()):
        return [note + " — a refresh will not overwrite them; upgrade ddflow"]
    return [
        note + " — `ddflow adopt --refresh-docs` rewrites them (and the rules blocks) and"
        " nothing else"
        + ("; upgrade ddflow for the newer ones" if AD.DOC_NEWER in lagging.values() else "")
    ]


def _orphan_notes(events: list) -> list[str]:

    lost = len(SS.unadopted_orphans(events))
    if lost <= 0:
        return []
    return [
        f"{lost} prompt/note event(s) have no session id — "
        "`ddflow session adopt-orphans` attaches them to the nearest session"
    ]


def _launcher_findings(repo: Path, problems: list[str], notes: list[str]) -> None:
    """A launcher recorded in a hook or MCP entry whose target is gone. The git hooks fail
    open now, so the check silently stops: a PROBLEM, unless `ddflow` on PATH still runs
    it (B-dangling-precommit-hook)."""

    for d in LA.findings(repo):
        (notes if d.fallback else problems).append(d.render())


#: How many uncommitted shards doctor names before it summarises.
_SHARDS_NAMED = 3


def _loose_shards(repo: Path) -> list[str]:
    """Event shards git has not committed: a clone or a pull gets an incomplete log
    (Bcd3512c891)."""

    loose = EC.uncommitted_shards(repo)
    if loose is None:
        return ["could not tell whether the event shards are committed: git status failed"]
    if not loose:
        return []
    named = ", ".join(loose[:_SHARDS_NAMED]) + (", ..." if len(loose) > _SHARDS_NAMED else "")
    return [
        f"{len(loose)} event shard(s) not committed ({named}): a clone or a pull gets an "
        "incomplete log; `git add .ddflow/events && git commit -m 'events' -- .ddflow/events`"
    ]


def doctor(repo: Path, *, agent: str = "", parser: Any = None, tools: Any = None) -> O.Outcome:
    """Everything that is wrong, and everything worth knowing. Exit 1 on any problem.

    Gathers from six sources — the log's own integrity, the dependency graph, the
    workflow's coherence, container reachability, the loop detector and the lease sweep —
    and splits each finding into a PROBLEM (something is broken) or a NOTE (something you
    should know). The split is the whole value: a report where a stale index reads the
    same as a dependency cycle is a report nobody acts on.

    The body is PROSE on both surfaces, rendered by `views/human.py`, because a list of
    problems with advice attached is what an operator and an agent both want.
    """

    log, cfg, st = _load(repo, agent)
    # One read of the whole log serves the data repairs and every pass below (the warm
    # parse cache makes it a digest rather than a re-parse, see infra/log.py), and saves
    # the gate-rate pass from folding again.
    repair_ctx = RP.context(repo, log, cfg)
    events = repair_ctx.events
    store = Store(repo, cfg)
    # The log's integrity, less what a data repair has quarantined (services.repairs).
    problems, notes = RP.integrity(repair_ctx)
    # A NOTE, not a problem: the log is append-only, so a shard that once had two
    # writers says so forever, and a doctor that can never pass again is one nobody
    # reads. The clock already reads past it (B190); the shared identity is the fix.
    for shard, (n, before, after) in log.clock_regressions().items():
        notes.append(
            f"{shard}: Lamport clock decreases at event {n} ({before} -> {after}) — two "
            "clones wrote as one agent id; give each its own (`DDFLOW_AGENT`, "
            "`[agent].id`, or drop both to derive a per-clone id)"
        )
    notes.extend(unknown_tier_notes(st.items.values()))
    notes.extend(_orphan_notes(events))

    notes.extend(export_select.doctor_notes(repo, cfg, st))
    notes.extend(_export_target_notes(repo, cfg))
    notes.extend(RULEVIEW.notes(repo, cfg, st))
    notes.extend(EMB.doctor_notes(cfg))  # the [rag] extra: present or not, never silent

    # A NOTE: a host signal this platform cannot supply only narrows what adaptive
    # parallelism steers by; it is never a failure.
    notes.extend(SIG.doctor_notes(SIG.HostSignals(repo)))

    # The ring the adaptive limit is folded from: unwritable or not git-ignored (notes).
    notes.extend(FL.doctor_notes(repo))
    notes.extend(FL.history_notes(cfg, events))  # log signals with too little history yet
    if not (repo / ".ddflow").exists():
        problems.append("no .ddflow directory — run `ddflow init`")
    _primary_mid_merge(repo, problems, notes)
    if store.stale(log):
        notes.append("index is stale; it rebuilds automatically on next read")
    notes += _loose_shards(repo)
    # Loaded past, not refused (config._apply) -- so this is where a typo still surfaces.
    # A KNOWN knob with a value this code does not know is an INVALID VALUE, not an
    # unknown key (Bf3566bbacd) -- asked of the config's bookkeeping, never of the entry's
    # text, which holds the user's own spelling (roborev on 183bcf03 and eb6a482d).
    CC.report(repo, cfg, problems, notes)
    # Events this code has no handler for: the fold skipped them (B168), so every number
    # below is computed WITHOUT them. A note, as an unknown config knob is named but the
    # old code keeps working -- the remedy is the same: bring in the newer ddflow.
    if st.skipped_kinds:
        notes.append(UP.skipped_kinds_advice(st))

    notes.extend(UP.doctor_notes(st))
    notes.extend(fold_problem_notes(st))

    p = plan_for(repo, log, cfg, st, purpose="structure", agent=log.agent_id)
    problems += ["dependency cycle: " + " -> ".join(cyc) for cyc in p.cycles]
    # B191: a merge brought in a rival definition or a rival claim. Every state, not only
    # open ones -- a finished item whose other definition was dropped is still lost work.
    problems += [
        f"{it.id} is CONTESTED: {it.contest_summary()} — "
        f"`ddflow show {it.id}`, then `ddflow resolve {it.id} --keep <event-id|agent>`"
        for it in sorted(st.items.values(), key=lambda i: i.id)
        if not it.removed and it.contest_summary()
    ]

    # B19: work that EXISTS and that `next` can never offer. Every other check here
    # measures the items that are present; this one asks whether any of them can be picked
    # up, which is the question that went unasked while 37 filed follow-ups sat invisible
    # on the source project with every audit exiting 0.

    for u in unpickable(st, cfg):
        if u.kind == "finished_phase":
            u.detail = _finished_phase_remedy(u.detail, st, cfg, u.item, repo)
        (problems if u.severity == "problem" else notes).append(u.render())

    # B24/B25: does ddflow's own machinery fire? Both are NOTES, not problems — a flaky
    # gate and a stalled cadence are facts about the tooling, and failing `doctor` on them
    # would block work on a defect in the thing that checks the work.

    notes += [f"gate {f.gate} {f.detail}" for f in RT.failing_gates(RT.gate_rates(events), cfg)]
    notes += [f"cadence behind schedule: {r.render()}" for r in RT.stalled(st, cfg)]
    _dependency_findings(repo, cfg, st, problems, notes)
    notes += _stale_glob_notes(repo, st)
    # Shared files (D-shared-globs): an append-only glob git does not union-merge, and a
    # shared generated file with no merge strategy at all.

    shared_problems, shared_notes = SF.findings(repo, cfg)
    problems += shared_problems
    notes += shared_notes

    # The workflow's own coherence. A pipeline naming a gate that has no definition is the
    # one config error that is both silent and permanent -- every item entering the
    # pipeline blocks on an outcome that can never be recorded -- so it belongs in the
    # command an operator runs when something is wrong, not only in `ddflow workflow`.
    for f in WF.check(cfg, load_gates(repo, cfg), repo):
        # `f.subject: f.detail`, not `f.render()` -- doctor prefixes its own severity, and
        # "PROBLEM: [problem] ..." reads like a bug in the tool reporting the bug.
        (problems if f.level == WF.PROBLEM else notes).append(f"{f.subject}: {f.detail}")

    # The reviewer endpoints are fetched HERE and handed down: `infra.container` must not
    # reach up into `services.review` to get them.
    try:
        urls = [(r.name, r.base_url) for r in load_reviewers(repo) if r.enabled]
    except Exception:
        urls = []
    notes.extend(CT.warnings(repo, cfg, urls))

    # The rules surface. MISSING is a PROBLEM: an agent with no project rules does not know
    # it must claim before editing, and every coordination guarantee here rests on that. A
    # drifted or stripped block is a note — the agent has rules, they are just not current.

    for state in rules_status(repo):
        if not state.needs_attention:
            continue
        # A block a newer ddflow wrote is not rewritten by a refresh: its own line says so.
        line = (
            state.render()
            if state.newer
            else (
                f"{state.render()} — `ddflow adopt --refresh-docs` rewrites it "
                "(plain `ddflow adopt` also rewrites MCP launches)"
            )
        )
        # NOT_BINDING sits with MISSING: a rule the agent may never load is not a
        # milder version of a drifted one, it is the mechanism switched off.
        severe = state.state in (MISSING, NOT_BINDING)
        (problems if severe else notes).append(line)

    _launcher_findings(repo, problems, notes)
    found, extra = stale_references(repo, parser, tools)
    problems += found
    notes += extra
    notes += _driver_drift_notes(repo)
    notes += _unknown_author_notes(repo, log, repair_ctx)
    notes += RP.doctor_notes(repair_ctx, skip=RP.DOCTOR_WORDED)

    for f in PR.detect(log.read_all(), st, cfg):
        (problems if f.severity == "block" else notes).append(f.render())
    for r in L.scan(log, cfg, repo):
        # A tree holding work or one git could not measure; a RUNNING item nobody holds
        # stays a note, as `next` offers it to resume (B3f8c406fea).
        tree_at_risk = may_hold_work(r) and r.kind != "stale_running"
        (problems if tree_at_risk else notes).append(f"{r.kind}: {r.item} — {r.advice}")

    for path in CL.unclaimed_trees(repo, cfg, st):
        notes.append(f"worktree {path} exists but no item claims it")
    notes += _untitled(st)
    notes += _dupe_note(st, cfg)

    data: dict[str, Any] = {
        "problems": problems,
        "notes": notes,
        "events": st.event_count,
        "items": len(st.items),
        "agent": log.agent_id,
        "repo": str(repo),
        "index_stale": store.stale(log),
        "fts": bool(store.fts),
    }
    out = (
        O.failed("doctor", f"{len(problems)} problem(s)", **data)
        if problems
        else O.ok("doctor", **data)
    )
    out.data["text"] = human.render(out)
    return out


def _only_ids(title: str) -> bool:
    """Empty, or nothing but id-shaped tokens and punctuation: `B30`, `B30.`, `+ B159`,
    `B62-B64`. A word without a digit is what makes a title say something."""
    return all(any(c.isdigit() for c in word) for word in re.findall(r"\w+", title))


#: How many untitled ids one note names before it counts the rest.
_UNTITLED_SHOWN = 8


def _export_target_notes(repo: Path, cfg: Config) -> list[str]:
    """A NOTE for each selected export target that is stale or hand-edited (B-export-enforce).

    Not a problem: regenerating is one command, and a doctor that fails on a document the log
    has simply moved past would block work. `[enforce].generated_views = "off"` silences it.
    """
    if cfg.enforce.generated_views == "off" or not cfg.export.documents:
        return []

    try:
        rows = X.listing(repo, cfg)
    except ExportError as exc:
        return [f"export: could not check the selected documents ({exc})"]
    return [
        f"export: {r['target']} ({r['doc']}) is {r['state']}"
        + (f": {r['detail']}" if r.get("detail") else "")
        + (
            f" (`ddflow export {r['doc']} --update`)"
            if r["state"] == "stale"
            else " (`ddflow export --check`; regenerating needs --force)"
        )
        for r in rows
        if r["selected"] and r["state"] in ("stale", "hand-edited")
    ]


def _untitled(st) -> list[str]:
    """ONE note naming the items whose title says nothing but their id.

    A migration that cannot find a title writes the id (bug B9e8ca361ea: 24 backlog
    items were titled `B30`, `B157`...), and every board, brief and gate prompt then
    shows a number. Nothing else here asks whether an item can be told apart from it.
    """
    ids = sorted(it.id for it in st.items.values() if not it.removed and _only_ids(it.title))
    if not ids:
        return []
    shown = ", ".join(ids[:_UNTITLED_SHOWN]) + (" ..." if len(ids) > _UNTITLED_SHOWN else "")
    return [
        f"{len(ids)} item(s) have no title of their own, only the id: {shown} — "
        f"`ddflow update <id> --title ...`"
    ]


#: The inline duplicate count is bounded on purpose. The sweep is all-pairs, so its cost
#: grows with the log (measured: ~0.6 s at 640 records, ~3 s at ddflow's own 1559); a
#: `doctor` that pays seconds at every session start is one people stop running. Above
#: this many records `doctor` says so and points at `ddflow dupes` instead.
_DOCTOR_SWEEP_MAX = 1000
#: Stop the count once this many pairs are found; the note then says "at least".
_DOCTOR_PAIR_CAP = 200


def _dupe_note(st, cfg) -> list[str]:
    """How many near-duplicate pairs the log holds that nobody has settled (B-dupes-sweep).

    A NOTE, never a problem: below the ask threshold a score is a prompt to LOOK, not a
    verdict (R-dedupe-matchers), so failing `doctor` on one would be failing it on a
    question. Counted at the ASK threshold -- the actionable "likely duplicate" band,
    not the wide show floor -- and bounded by record count and pair cap so `doctor`
    stays fast; above the bound the note names `ddflow dupes` rather than paying the
    sweep. `off` skips it: a project that switched the check off does not want its cost.
    """
    if cfg.dedupe.on_match == "off":
        return []
    try:
        n_records = sum(1 for r in _sweep_records(st) if r["kind"] in cfg.dedupe.kinds)
        if n_records > _DOCTOR_SWEEP_MAX:
            return [
                f"{n_records} records: the inline near-duplicate count is skipped above "
                f"{_DOCTOR_SWEEP_MAX} — `ddflow dupes` lists the pairs"
            ]
        rows = pairs_from(
            st,
            cfg,
            floor=cfg.dedupe.ask_threshold,
            limit=_DOCTOR_PAIR_CAP,
            stop_after=_DOCTOR_PAIR_CAP,
        )
    except Exception as exc:  # an unreadable index must not take the report down
        return [f"near-duplicate sweep could not run ({type(exc).__name__}: {exc})"]
    if not rows:
        return []
    n = f"at least {_DOCTOR_PAIR_CAP}" if len(rows) >= _DOCTOR_PAIR_CAP else str(len(rows))
    return [
        f"{n} unsettled near-duplicate pair(s) at the ask threshold — `ddflow dupes` "
        f"lists them; `ddflow link A --duplicate-of B` settles one (or `--distinct`)"
    ]


#: How many fold problems doctor names one by one; the rest are counted.
FOLD_PROBLEMS_SHOWN = 5


def fold_problem_notes(st) -> list[str]:
    """Doctor's lines for events the fold could not apply (`State.fold_problems`,
    B-uni-compat-events). A NOTE, like a skipped kind: the numbers doctor reports are
    computed without (the rest of) these events, and the log is append-only, so the remedy is a
    corrective event or a ddflow fix -- not something a re-run clears."""
    probs = list(getattr(st, "fold_problems", ()) or ())
    if not probs:
        return []
    lines = [
        f"{len(probs)} event(s) could not be folded: what each would have changed is "
        "missing from the counts below, and what its handler changed before it raised "
        "stays in (the log keeps them; a ddflow bug or a hand-edited shard -- file it with "
        "`ddflow bug found`):"
    ]
    lines += [
        f"  {p.event or '(no id)'} {p.kind} at lamport {p.lamport} by {p.agent or '?'}: {p.error}"
        for p in probs[:FOLD_PROBLEMS_SHOWN]
    ]
    if len(probs) > FOLD_PROBLEMS_SHOWN:
        lines.append(f"  ... and {len(probs) - FOLD_PROBLEMS_SHOWN} more")
    return ["\n".join(lines)]
