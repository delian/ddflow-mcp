"""Read-only questions about the queue and the log. None of these write."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..core.model import fold
from ..core.plain import plain
from ..infra.log import EventLog
from ._base import _load


def loops(repo: Path) -> O.Outcome:
    """Circular references and runtime loops. Reads only.

    The shape B37 is about: ONE description of the result, from which both surfaces
    derive their view. `cmd_loops` used to build the JSON body and the human paragraph
    independently — two renderings of one answer, kept in step by hand, which is how
    `cmd_complete` came to print a coverage gap to humans only.

    Exit stays as it was: 1 when there are findings, 2 when there are none. "Loops
    found" is a finding rather than a tool failure, but that contract is what callers
    already branch on and changing it silently would be worse than its imperfection.
    """
    from ..core import progress as PR

    # cfg BEFORE the log, not after: the log needs `[log]` to honour `reuse_parsed`.
    cfg = Config.load(repo)
    log = EventLog(repo, log_cfg=cfg.log)
    events = log.read_all()
    st = fold(events, strict=False)
    findings = [f.__dict__ for f in PR.detect(events, st, cfg)]
    data: dict[str, Any] = {
        "findings": findings,
        "events": len(events),
        "items": len(st.items),
        "blocking": [f for f in findings if f.get("severity") == "block"],
        "checked": [
            "dependency cycles",
            "repeat claims",
            "gate flapping",
            "repeated failures",
            "reopened items",
            "duplicate work",
            "stalled queue",
        ],
    }
    if not findings:
        return O.nothing("loops", "no loops detected", **data)
    n, b = len(findings), len(data["blocking"])
    return O.failed("loops", f"{n} finding(s)" + (f", {b} blocking" if b else ""), **data)


def progress(repo: Path, item: str = "") -> O.Outcome:
    """What work has actually been done, aggregated from the log. Reads only.

    The wire body is the ROW ARRAY, exactly as `ddflow progress --json` emits it — see
    `MIGRATED_WIRE_SHAPES`. The extra keys here are for the human renderer and for
    callers that want the count without walking the list; the `payload` entry on the
    tool keeps the MCP body unchanged.
    """
    from ..core import progress as PR

    log = EventLog(repo, log_cfg=Config.load(repo).log)
    events = log.read_all()
    st = fold(events, strict=False)
    rows = [r for r in PR.work(events, st).values() if not item or r.item == item]
    rows.sort(key=lambda r: (-r.total_seconds, r.item))
    data: dict[str, Any] = {
        "rows": [r.summary() for r in rows],
        "count": len(rows),
        "item": item,
    }
    if item and not rows:
        return O.failed("progress", f"no such item {item!r}", **data)
    if not rows:
        return O.nothing("progress", "no work recorded yet", **data)
    return O.ok("progress", **data)


#: How many entries each of `status`'s lists carries unless the caller asks for them all.
#: The counts are always exact; a 4831-task queue listed every finished task, 721k chars,
#: past what an MCP client accepts as one tool result (Bd6aa9ffde9).
STATUS_LIST_LIMIT = 25


def status(repo: Path, *, agent: str = "", full: bool = False) -> O.Outcome:
    """One answer to "what is the state of this project?".

    ``full`` lists everything; otherwise each list is cut to ``STATUS_LIST_LIMIT`` (the
    most recently completed tasks, newest last; the first of the others, in the
    scheduler's order) and
    ``truncated`` names each cut list with its real length. The CLI asks for ``full``; the
    MCP tool takes the bounded answer.

    The textbook B37 case: `cmd_status` folded the log, aggregated the work, detected the
    loops, planned and scanned for recoverables — and then built a JSON object and a
    prose summary from that ONE computation, separately, by hand. Two of the numbers
    appeared in only one of them.
    """
    from ..core import progress as PR
    from ..core.schedule import plan
    from ..services import leases as L

    log, cfg, _ = _load(repo, agent)
    events = log.read_all()
    st = fold(events, strict=False)
    tracked = PR.work(events, st)
    findings = PR.detect(events, st, cfg)
    p = plan(st, cfg, agent=log.agent_id)
    rec = L.scan(log, cfg, repo)

    phases, tasks = st.phases(), st.tasks()
    # Every bucket is read off the ONE plan `next` and `brief` use, so each task is in
    # exactly one and `total` is their sum (Bdcce70d036: "blocked" counted reason
    # "deps" alone, and every item a parallelism cap held back was in no bucket at all).
    done = [t for t in tasks if t.state == "done"]
    abandoned = [t for t in tasks if t.state == "abandoned"]
    running = p.running
    capped = [st.items[i] for i in p.capped]
    blocked = [b for b in p.blocked if b.item not in set(p.capped)]
    hours = sum(w.total_seconds for w in tracked.values()) / 3600
    commits = sum(len(w.commits) for w in tracked.values())
    live_decisions = [d for d in st.decisions.values() if d.live]
    open_bugs = [b for b in st.bugs.values() if b.open]

    data: dict[str, Any] = {
        "phases": {"total": len(phases), "done": sum(1 for x in phases if x.state == "done")},
        "tasks": {
            "total": len(tasks),
            "done": len(done),
            "running": len(running),
            "ready": len(p.ready),
            "held_by_cap": len(capped),
            "blocked": len(blocked),
            "review": len(p.review),
            "abandoned": len(abandoned),
        },
        "completed_tasks": [
            {"id": t.id, "title": t.title, "sha": t.merged_sha}
            for t in sorted(done, key=lambda t: t.completed_at)
        ],
        "in_flight": [
            {"id": t.id, "title": t.title, "holder": t.lease.holder if t.lease else ""}
            for t in running
        ],
        # A task RUNNING with no live lease is offered as ready -- someone must resume it
        # -- but never silently: its worktree may hold uncommitted work. `next` and
        # `brief` say so; so does this (roborev, job 897).
        "ready_now": [
            {
                "id": t.id,
                "title": t.title,
                **({"interrupted": True} if t.state == "running" else {}),
            }
            for t in p.ready
        ],
        "interrupted": p.interrupted,
        "held_by_cap": [{"id": t.id, "title": t.title} for t in capped],
        "cap": p.cap_note if capped else "",
        "agent_hours": round(hours, 2),
        "commits": commits,
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
        "loops": [f.__dict__ for f in findings],
        "recoverable": [plain(r) for r in rec if r.salvageable],
    }
    if st.skipped_kinds:
        data["skipped_kinds"] = dict(st.skipped_kinds)
    if not full:
        _bound(data)
    # Carried for the prose view, which needs the OBJECTS (`completed_at` to sort by, the
    # blocked ids, how many recoverables are not salvageable) rather than a second fold.
    # Under `_render`, never on the wire.
    data["_render"] = {
        "repo": repo.name,
        "done": done,
        "running": running,
        "ready": p.ready,
        "interrupted": p.interrupted,
        "capped": capped,
        "cap": p.cap_note,
        "blocked": blocked,
        "recoverable": rec,
        "findings": findings,
        "hours": hours,
        "tasks": len(tasks),
        "phases": len(phases),
        "decisions": len(live_decisions),
        "lessons": len(st.lessons),
        "open_bugs": len(open_bugs),
    }
    return O.ok("status", **data)


def _bound(data: dict[str, Any]) -> None:
    """Cut `status`'s lists to `STATUS_LIST_LIMIT`, saying which were cut and from what."""
    cut: dict[str, int] = {}
    lists = ("completed_tasks", "in_flight", "ready_now", "held_by_cap", "interrupted")
    for key in (*lists, "loops", "recoverable"):
        rows = data[key]
        if len(rows) > STATUS_LIST_LIMIT:
            cut[key] = len(rows)
            # The most recent completions are the ones a reader asks about -- kept in
            # completion order, newest last, as the full list has them; for the others
            # the scheduler's order puts what to do first at the top.
            keep = (
                slice(-STATUS_LIST_LIMIT, None)
                if key == "completed_tasks"
                else slice(STATUS_LIST_LIMIT)
            )
            data[key] = rows[keep]
    if cut:
        data["truncated"] = {
            "lists": cut,
            "shown": STATUS_LIST_LIMIT,
            "all": "`ddflow --json status` lists every entry; `tasks` counts are exact",
        }


def rebuild(repo: Path, *, agent: str = "") -> O.Outcome:
    """Rebuild the sqlite projection from the log. The log is the source of truth; this
    is a cache, and saying how long it took is how you notice it has stopped being one."""
    import time

    from ..infra.store import Store

    log, cfg, _ = _load(repo, agent)
    t0 = time.time()
    st = Store(repo, cfg).rebuild(log)
    return O.ok(
        "rebuild",
        events=st.event_count,
        items=len(st.items),
        lessons=len(st.lessons),
        seconds=round(time.time() - t0, 2),
    )


#: How much of one record's text a rendering carries. The record is whole in the log and
#: in `show --json`; a brief is budgeted.
_TEXT_CAP = 600


def _epoch(ts: str) -> float:
    """An event's ISO timestamp as epoch seconds; 0.0 for one that does not parse."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def record_summary(st, rid: str) -> dict[str, Any]:
    """What a link points at, in one dict: its kind, title, state and own text."""
    if rid in st.items:
        i = st.items[rid]
        return {"kind": i.kind, "title": i.title or "", "state": i.state, "text": i.body or ""}
    if rid in st.bugs:
        b = st.bugs[rid]
        return {"kind": "bug", "title": "", "state": b.resolution or "open", "text": b.summary}
    if rid in st.lessons:
        ls = st.lessons[rid]
        return {"kind": "lesson", "title": ls.title, "state": "", "text": ls.text()}
    for kind, pool in (
        ("research", st.research),
        ("decision", st.decisions),
        ("memory", st.memories),
    ):
        if rid in pool:
            r = pool[rid]
            # `text` is a field on a memory and a METHOD on a decision: call what is callable.
            text = (
                getattr(r, "summary", "")
                or getattr(r, "text", "")
                or getattr(r, "claim", "")
                or getattr(r, "question", "")
                or ""
            )
            return {
                "kind": kind,
                "title": getattr(r, "title", "") or getattr(r, "question", "") or "",
                "state": "",
                "text": text() if callable(text) else text,
            }
    return {"kind": "", "title": "", "state": "", "text": ""}


def addenda(st, rid: str) -> dict[str, Any]:
    """Everything the log says was ADDED to, or LINKED to, one record (D-no-duplicates).

    `additions`: the verbatim `record.extended` texts, oldest first. `links`: what this
    record points at. `linked_from`: the records that point at it -- found by scanning
    every record's links for this target, because a new record filed `extends` /
    `duplicate_of` X is stored as a link ON THE NEW RECORD; State keeps no index under X
    and only `related` also writes a back-link.
    """
    mine = st.links.get(rid)
    links = []
    for x in mine.links if mine else []:
        links.append(
            {**x, **{k: v for k, v in record_summary(st, x["target"]).items() if k != "text"}}
        )
    out_related = {x["target"] for x in links if x["relation"] == "related"}
    inbound = []
    for src, rl in st.links.items():
        if src == rid:
            continue
        for x in rl.links:
            if x["target"] != rid:
                continue
            if x["relation"] == "related" and src in out_related:
                continue  # the back-link already listed it
            inbound.append({**x, "record": src, **record_summary(st, src)})
    inbound.sort(key=lambda x: (x["at"], x["event"], x["record"]))
    return {
        "additions": list(mine.extensions) if mine else [],
        "links": links,
        "linked_from": inbound,
    }


def new_reports(st, rid: str, since: float) -> dict[str, Any]:
    """The additions and inbound links on `rid` made at or after `since` (epoch seconds):
    what was reported against an item after its holder claimed it."""
    a = addenda(st, rid)
    adds = [x for x in a["additions"] if _epoch(x["at"]) >= since]
    linked = [x for x in a["linked_from"] if _epoch(x["at"]) >= since]
    # A `related` record writes a back-link on X (a later `link.recorded`, never an add), which `addenda` lists once (as X's own
    # link) and not again as inbound -- but for a holder it IS a new report.
    for x in a["links"]:
        if x["relation"] == "related" and x["source"] == "later" and _epoch(x["at"]) >= since:
            linked.append(
                {**x, "record": x["target"], "text": record_summary(st, x["target"])["text"]}
            )
    return {"count": len(adds) + len(linked), "additions": adds, "linked_from": linked}


def show(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """One item, with its gate status -- or one bug, by its id. The wire body is the item
    (or the bug's record) itself.

    Worktree paths are absolutised on the way out: the log stores them RELATIVE to the
    repo root, which is what makes a committed log true on every checkout, but a caller
    handed ".ddflow-worktrees/T1" has to know what it is relative to and will resolve it
    against its own cwd.
    """
    from ..infra.worktree import absolutise
    from ..services import gates as G

    _log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None and item in st.bugs:
        return _show_bug(st, st.bugs[item])
    if it is None or it.removed:
        if it is not None:
            return O.failed(
                "show", f"no such item {item!r} (it was removed from the queue)", id=item, item=None
            )
        return O.failed("show", f"no such item or bug {item!r}", id=item, item=None)
    return O.ok(
        "show",
        id=item,
        item={**absolutise(repo, plain(it)), **addenda(st, item)},
        gates=plain(G.status(st, cfg, item)),
        _render={
            "item": it,
            "gate_status": G.status(st, cfg, item),
            "addenda": addenda(st, item),
        },
    )


def _show_bug(st, bug) -> O.Outcome:
    """A bug id handed to `show` (B-show-bug-id): its record, its state, and the items
    that fix it -- those whose title or body say "fixes bug X" (or "fixes bugs A, X",
    "Fixing X"), the claim every fix task carries; any other mention is listed apart. The wire body (`item`, as for an item) is the record itself."""
    named = re.compile(rf"(?<![\w-]){re.escape(bug.id)}(?![\w-])")
    # The convention fix tasks follow: "fixes bug X", "fixes bugs A, B and X", "Fixing X"
    # -- the verb, then the id or a list holding it, with nothing else in between.
    claims_fix = re.compile(
        rf"\bfix(?:es|ed|ing)?\s+(?:bugs?\s+)?(?:[\w-]+\s*,\s*)*(?:and\s+)?"
        rf"{re.escape(bug.id)}(?![\w-])",
        re.I,
    )
    fixing: list[str] = []
    mentions: list[str] = []
    for i in sorted(st.items.values(), key=lambda x: x.id):
        text = f"{i.title or ''}\n{i.body or ''}"
        if i.removed or not named.search(text):
            continue
        # "fixes bug X" in the title or body is the fix's own claim; any other mention is
        # only that -- a task that discusses a bug is not its fix (roborev, job 954).
        (fixing if claims_fix.search(text) else mentions).append(i.id)
    record = {
        **plain(bug),
        "kind": "bug",
        "state": bug.resolution or "open",
        "fixing": fixing,
        "mentioned_by": mentions,
        **addenda(st, bug.id),
    }
    return O.ok("show", id=bug.id, item=record, _render={"bug": record})


def recover(repo: Path, *, item: str = "", apply: bool = False, agent: str = "") -> O.Outcome:
    """Crashed agents' leases and orphaned worktrees. Exit 2 when there is nothing.

    A worktree with uncommitted changes is reported and never touched, whatever `apply`
    says — the whole value of the sweep is that it does not destroy the work it found.
    """
    from ..infra.worktree import absolutise
    from ..services import leases as L

    log, cfg, _ = _load(repo, agent)
    found = [r for r in L.sweep(log, cfg, repo, apply=apply) if not item or r.item == item]
    salvageable = [r for r in found if r.salvageable]
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
    from ..core.schedule import is_external
    from ..services import external as EX

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


def _finished_phase_remedy(detail: str, st, cfg: Config, item: str, repo: Path) -> str:
    """B5189cc5756: offer `ddflow complete` only when it would succeed.

    `unpickable()` (core) states the fact and the default remedy; whether completion is
    allowed is decided in exactly one place, `completion.verdict()`, so it is asked here
    rather than re-derived. `model=""` is harmless: the reviewer-independence blocker is
    applied to tasks only, never to a phase, so an unknown author model cannot put a
    spurious blocker into this remedy.
    """
    from ..services.completion import verdict

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
    from ..infra import worktree as W

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


def _driver_drift_notes(repo: Path) -> list[str]:
    """One note naming driver docs that differ from the templates this ddflow ships."""
    from ..services.adopt import driver_drift

    lagging = driver_drift(repo)
    if not lagging:
        return []
    return [
        f"driver docs differ from the templates this ddflow ships: {', '.join(lagging)}"
        " — `ddflow adopt --refresh-docs` rewrites them (and the rules blocks) and nothing else"
    ]


def doctor(repo: Path, *, agent: str = "") -> O.Outcome:
    """Everything that is wrong, and everything worth knowing. Exit 1 on any problem.

    Gathers from six sources — the log's own integrity, the dependency graph, the
    workflow's coherence, container reachability, the loop detector and the lease sweep —
    and splits each finding into a PROBLEM (something is broken) or a NOTE (something you
    should know). The split is the whole value: a report where a stale index reads the
    same as a dependency cycle is a report nobody acts on.

    The body is PROSE on both surfaces, rendered by `views/human.py`, because a list of
    problems with advice attached is what an operator and an agent both want.
    """
    from ..core import progress as PR
    from ..core.schedule import plan
    from ..infra import container as CT
    from ..infra import worktree as W
    from ..infra.store import Store
    from ..services import leases as L
    from ..services import workflow as WF
    from ..services.gates import load_gates
    from ..views import human

    log, cfg, st = _load(repo, agent)
    # `verify()` already reads the whole log, and the warm parse cache makes a second read
    # a digest rather than a re-parse (see infra/log.py), so this costs a few ms and saves
    # the gate-rate pass from folding again.
    events = log.read_all()
    store = Store(repo, cfg)
    problems: list[str] = list(log.verify())
    notes: list[str] = []
    # A NOTE, not a problem: the log is append-only, so a shard that once had two
    # writers says so forever, and a doctor that can never pass again is one nobody
    # reads. The clock already reads past it (B190); the shared identity is the fix.
    for shard, (n, before, after) in log.clock_regressions().items():
        notes.append(
            f"{shard}: Lamport clock decreases at event {n} ({before} -> {after}) — two "
            "clones wrote as one agent id; give each its own (`DDFLOW_AGENT`, "
            "`[agent].id`, or drop both to derive a per-clone id)"
        )
    if not (repo / ".ddflow").exists():
        problems.append("no .ddflow directory — run `ddflow init`")
    _primary_mid_merge(repo, problems, notes)
    if store.stale(log):
        notes.append("index is stale; it rebuilds automatically on next read")
    # Loaded past, not refused (config._apply) -- so this is where a typo still surfaces.
    problems += [
        f"unknown config key {k} in .ddflow/config.toml: a typo, or written by a newer "
        "ddflow than this checkout runs (merge main)"
        for k in cfg.unknown_knobs
    ]
    # Events this code has no handler for: the fold skipped them (B168), so every number
    # below is computed WITHOUT them. A note, as an unknown config knob is named but the
    # old code keeps working -- the remedy is the same: bring in the newer ddflow.
    if st.skipped_kinds:
        kinds = ", ".join(f"{k} x{n}" for k, n in sorted(st.skipped_kinds.items()))
        notes.append(
            f"this log has events from a newer ddflow than this checkout runs, skipped: "
            f"{kinds} (merge main, or run the newer ddflow)"
        )

    p = plan(st, cfg, agent=log.agent_id)
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
    from ..core.schedule import unpickable

    for u in unpickable(st, cfg):
        if u.kind == "finished_phase":
            u.detail = _finished_phase_remedy(u.detail, st, cfg, u.item, repo)
        (problems if u.severity == "problem" else notes).append(u.render())

    # B24/B25: does ddflow's own machinery fire? Both are NOTES, not problems — a flaky
    # gate and a stalled cadence are facts about the tooling, and failing `doctor` on them
    # would block work on a defect in the thing that checks the work.
    from ..services import rates as RT

    notes += [f"gate {f.gate} {f.detail}" for f in RT.failing_gates(RT.gate_rates(events), cfg)]
    notes += [f"cadence behind schedule: {r.render()}" for r in RT.stalled(st, cfg)]
    _dependency_findings(repo, cfg, st, problems, notes)
    # Shared files (D-shared-globs): an append-only glob git does not union-merge, and a
    # shared generated file with no merge strategy at all.
    from ..services import shared_files as SF

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
        from ..services.review import load_reviewers

        urls = [(r.name, r.base_url) for r in load_reviewers(repo) if r.enabled]
    except Exception:
        urls = []
    notes.extend(CT.warnings(repo, cfg, urls))

    # The rules surface. MISSING is a PROBLEM: an agent with no project rules does not know
    # it must claim before editing, and every coordination guarantee here rests on that. A
    # drifted or stripped block is a note — the agent has rules, they are just not current.
    from ..services.adopt import MISSING, NOT_BINDING, rules_status

    for state in rules_status(repo):
        if not state.needs_attention:
            continue
        line = (
            f"{state.render()} — `ddflow adopt --refresh-docs` rewrites it "
            "(plain `ddflow adopt` also rewrites MCP launches)"
        )
        # NOT_BINDING sits with MISSING: a rule the agent may never load is not a
        # milder version of a drifted one, it is the mechanism switched off.
        severe = state.state in (MISSING, NOT_BINDING)
        (problems if severe else notes).append(line)

    notes += _driver_drift_notes(repo)

    for f in PR.detect(log.read_all(), st, cfg):
        (problems if f.severity == "block" else notes).append(f.render())
    for r in L.scan(log, cfg, repo):
        (problems if r.salvageable else notes).append(f"{r.kind}: {r.item} — {r.advice}")

    known = {str(W.load_path(repo, it.worktree)) for it in st.items.values() if it.worktree}
    for w in W.list_worktrees(repo):
        path = w.get("worktree", "")
        if (
            path
            and cfg.worktree.branch_prefix.rstrip("/") in w.get("branch", "")
            and path not in known
        ):
            notes.append(f"worktree {path} exists but no item claims it")
    notes += _untitled(st)

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


def board(repo: Path, *, phase: str = "", agent: str = "") -> O.Outcome:
    """The queue as a board: the markdown document (`text`, what MCP and a terminal
    show) and the same rows as data (`phases`, `critical_path`) for `--json` -- which
    used to print the markdown (B1f1d4f9f54)."""
    from ..core.schedule import critical_path
    from ..services.gates import pipeline_for
    from ..views import markdown as render_md
    from .lifecycle import _unknown_phase

    _log, cfg, st = _load(repo, agent)
    unknown = _unknown_phase(st, phase, phases_only=True)
    if unknown:  # as `next` refuses it, not an empty board (Bc2acd426f4)
        return O.failed("board", unknown, phase=phase, text="")
    phases = []
    for ph in sorted(st.phases(), key=lambda p: (p.priority, p.id)):
        if phase and ph.id != phase:
            continue
        tasks = [
            {
                "id": t.id,
                "title": t.title,
                "state": t.state,
                "parent": t.parent,
                "depth": render_md._depth(st, t, ph.id),
                "needs": list(t.needs),
                "globs": list(t.globs),
                "owner": t.lease.holder if t.lease else "",
                "gates": {g: t.gate_outcome(g) for g in pipeline_for(t, cfg)},
            }
            for t in render_md._nested(st, ph.id)
        ]
        phases.append(
            {
                "id": ph.id,
                "title": ph.title,
                "state": ph.state,
                "needs": list(ph.needs),
                "holder": ph.lease.holder if ph.lease else "",
                "tasks": tasks,
            }
        )
    return O.ok(
        "board",
        text=render_md.board(st, cfg, phase=phase),
        phase=phase,
        phases=phases,
        critical_path=critical_path(st, phase),
    )


#: `render --show <name>` targets, and the function that produces each.
#:
#: `--show` exists so the MCP `resources/read` handler can serve these through one code
#: path like everything else. It used to fold the log itself -- a second data path in a
#: module whose whole premise is "one implementation, two doors".
_RENDERABLE = ("lessons", "lessons-summary", "research", "board")

#: Where `render` writes its views when no directory is given.
DEFAULT_RENDER_DIR = "docs/ddflow"


def render(
    repo: Path, *, show: str = "", out_dir: str = DEFAULT_RENDER_DIR, agent: str = ""
) -> O.Outcome:
    """One named view as a document, or every view written to disk.

    Two shapes by design, and both are pre-existing contracts: `--show` returns the
    document, and without it the answer is the list of files written.
    """
    from ..views import markdown as render_md

    log, cfg, st = _load(repo, agent)
    if show:
        if show not in _RENDERABLE:
            return O.failed(
                "render",
                f"unknown view {show!r}; known: {', '.join(sorted(_RENDERABLE))}",
                show=show,
                text="",
                files=[],
            )
        fn = {
            "lessons": render_md.lessons_md,
            "lessons-summary": render_md.lessons_summary_md,
            "research": render_md.research_md,
            "board": render_md.board,
        }[show]
        # `board` takes the config; the two markdown views do not. INSPECTED rather than
        # try/except'd, because a TypeError raised inside a renderer would otherwise be
        # caught and retried with the wrong arity.
        import inspect

        text = fn(st, cfg) if len(inspect.signature(fn).parameters) > 1 else fn(st)
        return O.ok("render", show=show, text=text, files=[])

    from ..config import Config
    from ..infra.store import Store

    # Written views are rendered from the config FILES only, never `DDFLOW_*` env
    # overrides. A file that gets committed must be reproducible from what is committed,
    # or the pre-commit check (B18) refuses a correct render made under one environment
    # and committed under another -- blaming a hand edit that never happened.
    committed_cfg = Config.load(repo, env={})
    files = render_md.write_views(repo, Store(repo, cfg).ensure(log), committed_cfg, subdir=out_dir)
    return O.ok("render", show="", text="", files=[str(f) for f in files])


def replay(repo: Path, *, out_dir: str = "", verify: bool = False, agent: str = "") -> O.Outcome:
    """Reconstruct the project's history from the log alone.

    `verify` re-resolves every recorded commit: a sha that no longer resolves means the
    reconstruction describes work that is not in the tree, which is the one way this
    document can be confidently wrong.
    """
    from ..services import sessions as session

    log, cfg, st = _load(repo, agent)
    steps = session.replay(log.read_all())
    problems = session.verify(st, repo, cfg) if verify else []
    if out_dir:
        files = session.bundle(st, steps, Path(out_dir), cfg, project=repo.name)
        text = "wrote:\n" + "\n".join(f"  {f}" for f in files)
        return O.ok(
            "replay", text=text, files=[str(f) for f in files], problems=problems, verified=verify
        )
    return O.ok(
        "replay",
        text=session.render_reconstruction(st, steps, project=repo.name),
        files=[],
        problems=problems,
        verified=verify,
    )
