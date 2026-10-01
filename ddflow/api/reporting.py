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


def status(repo: Path, *, agent: str = "") -> O.Outcome:
    """One answer to "what is the state of this project?".

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
        "completed_tasks": [{"id": t.id, "title": t.title, "sha": t.merged_sha} for t in done],
        "in_flight": [
            {"id": t.id, "title": t.title, "holder": t.lease.holder if t.lease else ""}
            for t in running
        ],
        "ready_now": [{"id": t.id, "title": t.title} for t in p.ready],
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
    # Carried for the prose view, which needs the OBJECTS (`completed_at` to sort by, the
    # blocked ids, how many recoverables are not salvageable) rather than a second fold.
    # Under `_render`, never on the wire.
    data["_render"] = {
        "repo": repo.name,
        "done": done,
        "running": running,
        "ready": p.ready,
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


def show(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """One item, with its gate status. The wire body is the item itself.

    Worktree paths are absolutised on the way out: the log stores them RELATIVE to the
    repo root, which is what makes a committed log true on every checkout, but a caller
    handed ".ddflow-worktrees/T1" has to know what it is relative to and will resolve it
    against its own cwd.
    """
    from ..infra.worktree import absolutise
    from ..services import gates as G

    _log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("show", f"no such item {item!r}{gone}", id=item, item=None)
    return O.ok(
        "show",
        id=item,
        item=absolutise(repo, plain(it)),
        gates=plain(G.status(st, cfg, item)),
        _render={"item": it, "gate_status": G.status(st, cfg, item)},
    )


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
    if store.stale(log):
        notes.append("index is stale; it rebuilds automatically on next read")
    # Loaded past, not refused (config._apply) -- so this is where a typo still surfaces.
    problems += [
        f"unknown config key {k} in .ddflow/config.toml: a typo, or written by a newer "
        "ddflow than this checkout runs (merge main)"
        for k in cfg.unknown_knobs
    ]

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
        line = f"{state.render()} — `ddflow adopt` rewrites it"
        # NOT_BINDING sits with MISSING: a rule the agent may never load is not a
        # milder version of a drifted one, it is the mechanism switched off.
        severe = state.state in (MISSING, NOT_BINDING)
        (problems if severe else notes).append(line)

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
    """The queue as a markdown board. The body is the document, on both surfaces."""
    from ..views import markdown as render_md

    _log, cfg, st = _load(repo, agent)
    return O.ok("board", text=render_md.board(st, cfg, phase=phase), phase=phase)


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
