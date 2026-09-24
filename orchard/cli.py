"""The Orchard command line — the portable surface every agent can drive.

Design commitments, each of which exists so that ANY agent (or a human, or CI) can use
this without special support:

* **Everything is available as a subprocess call.** MCP is an optional convenience
  layer over the same functions, never a requirement. An agent with nothing but a shell
  can run the entire workflow.
* **``--json`` on every read command.** Agents parse; humans read. Offering both from
  one code path stops the two drifting.
* **A uniform exit vocabulary:** ``0`` healthy · ``1`` real failure · ``2`` could not
  run / nothing to do · ``3`` coordination refused. ``2`` is never collapsed into ``0``.
  "Nothing is ready" and "everything is fine" are different facts and an agent that
  cannot tell them apart will invent work.
* **Refusals name the alternative.** A command that says no says what to do instead;
  that is the difference between a blocked agent and a re-ordered one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from . import gates as G
from . import lease as L
from . import render, session
from . import worktree as W
from .config import Config
from .events import EventLog
from .model import GATE_OUTCOMES, fold
from .schedule import critical_path, plan
from .store import Store

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3
UNAVAILABLE_EXIT = NOTHING

#: How many offending files a refusal lists before summarising the rest. Enough to see
#: whether they are build artefacts or real source -- which is the judgement the
#: operator has to make -- without burying the remedy underneath them.
MAX_LISTED_FILES = 10

#: `worktree.merge`/`remove` return this to mean "refused on a precondition" as opposed
#: to "git failed". It maps to the CLI's REFUSED, and naming it keeps the two exit
#: vocabularies from being silently conflated.
GIT_REFUSED = 2


class Ctx:
    """Everything a command needs, resolved once."""

    def __init__(self, args: argparse.Namespace) -> None:
        start = Path(args.repo or os.environ.get("ORCHARD_REPO") or Path.cwd())
        try:
            self.repo = W.repo_root(start)
        except W.GitError:
            self.repo = start.resolve()
        self.cfg = Config.load(self.repo)
        # `ORCHARD_AGENT` is the short alias documented in server.json and used by MCP
        # clients and the git hook, which run in an environment where passing `--agent`
        # is not possible. It was documented before it was read -- and a dead env var
        # in a published manifest is worse than an undocumented one, because operators
        # set it and nothing happens.
        env_agent = os.environ.get("ORCHARD_AGENT", "")
        if args.agent:
            self.cfg.agent.id = args.agent
        elif env_agent and self.cfg.sources.get("agent.id", "default") == "default":
            self.cfg.agent.id = env_agent
            self.cfg.sources["agent.id"] = "env"
        self.log = EventLog(
            self.repo, self.cfg.agent.id, lock_timeout_s=self.cfg.lease.acquire_timeout_s
        )
        self.store = Store(self.repo, self.cfg)
        self.gates = G.load_gates(self.repo, self.cfg)
        self.json = bool(getattr(args, "json", False))

    def state(self):
        return fold(self.log.read_all(), strict=False)

    def out(self, human: str, data: Any = None) -> None:
        if self.json:
            print(
                json.dumps(
                    _plain(data if data is not None else {"message": human}), indent=2, default=str
                )
            )
        else:
            print(human)


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _auto_id(prefix: str, *parts: str) -> str:
    """A collision-free auto id.

    Second-resolution timestamps (`f"L{int(time.time())}"`) collide whenever two items
    are created in the same second -- which a script, a loop, or an agent recording two
    lessons from one bug hunt does routinely. The collision is SILENT: the second record
    overwrites the first in the fold, so the entry simply disappears. Measured: 7
    lessons added in one second, 2 survived.

    Content-addressed instead, so the id is stable for identical content and distinct
    for anything else, with a microsecond stamp to separate genuine duplicates.
    """
    seed = "|".join(parts) + f"|{time.time_ns()}"
    return prefix + hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()


# -- commands --------------------------------------------------------------------------


def cmd_init(a, c: Ctx) -> int:
    d = c.repo / ".orchard"
    d.mkdir(parents=True, exist_ok=True)
    (d / "events").mkdir(exist_ok=True)
    gi = d / ".gitignore"
    gi.write_text(
        "# The index and local state are DERIVED from events/ and are rebuildable.\n"
        "# They are machine-local on purpose: a committed index resurrects dead agents'\n"
        "# leases on every clone, and a committed cache is a merge conflict with no\n"
        "# meaningful resolution.\n"
        "index.db\nindex.db-*\nindex.rebuilding*\nevents.lock\nlocal/\n",
        "utf-8",
    )
    cfgp = d / "config.toml"
    if not cfgp.exists():
        cfgp.write_text(_starter_config(), "utf-8")
    ga = c.repo / ".gitattributes"
    line = ".orchard/events/*.jsonl merge=union\n"
    prev = ga.read_text("utf-8") if ga.exists() else ""
    if "orchard/events" not in prev:
        ga.write_text(prev + ("" if prev.endswith("\n") or not prev else "\n") + line, "utf-8")
    c.store.rebuild(c.log)
    c.out(
        f"Initialised Orchard in {d}\n"
        f"  config: {cfgp}\n"
        f"  Next: `orchard phase add P1 --title 'First phase'`",
        {"root": str(d), "config": str(cfgp)},
    )
    return OK


def cmd_phase_add(a, c: Ctx) -> int:
    c.log.append(
        "phase.added",
        a.id,
        {
            "title": a.title,
            "needs": _csv(a.needs),
            "globs": _csv(a.globs),
            "body": a.body or "",
            "tags": _csv(a.tags),
            "priority": a.priority,
        },
    )
    c.out(f"phase {a.id} added", {"id": a.id})
    return OK


def cmd_task_add(a, c: Ctx) -> int:
    st = c.state()
    if a.phase and a.phase not in st.items:
        print(
            f"no such phase {a.phase!r}. `orchard phase add {a.phase} --title ...` first",
            file=sys.stderr,
        )
        return FAIL
    c.log.append(
        "task.added",
        a.id,
        {
            "parent": a.phase,
            "title": a.title,
            "needs": _csv(a.needs),
            "globs": _csv(a.globs),
            "body": a.body or "",
            "tags": _csv(a.tags),
            "priority": a.priority,
        },
    )
    c.out(f"task {a.id} added to {a.phase or '(no phase)'}", {"id": a.id})
    return OK


def cmd_item_update(a, c: Ctx) -> int:
    st = c.state()
    it = st.items.get(a.id)
    if not it:
        print(f"no such item {a.id!r}", file=sys.stderr)
        return FAIL
    d: dict[str, Any] = {}
    for f in ("title", "body"):
        if getattr(a, f) is not None:
            d[f] = getattr(a, f)
    for f in ("needs", "globs", "tags"):
        if getattr(a, f) is not None:
            d[f] = _csv(getattr(a, f))
    if a.priority is not None:
        d["priority"] = a.priority
    if not d:
        print("nothing to update", file=sys.stderr)
        return NOTHING
    c.log.append("phase.updated" if it.kind == "phase" else "task.updated", a.id, d)
    c.out(f"{a.id} updated: {', '.join(d)}", {"id": a.id, "changed": list(d)})
    return OK


def cmd_next(a, c: Ctx) -> int:
    """Offer the next actionable item(s). Exit 2 when nothing is actionable."""
    st = c.state()
    p = plan(st, c.cfg, kind=a.kind, phase=a.phase or "", agent=c.cfg.agent.id or c.log.agent_id)
    if c.json:
        print(
            json.dumps(
                _plain(
                    {
                        "ready": [_plain(i) for i in p.ready],
                        "blocked": [_plain(b) for b in p.blocked],
                        "running": [i.id for i in p.running],
                        "cycles": p.cycles,
                        "critical_path": critical_path(st, a.phase or ""),
                    }
                ),
                indent=2,
                default=str,
            )
        )
        return OK if p.ready else NOTHING
    if p.cycles:
        print("DEPENDENCY CYCLE(S) — nothing can be scheduled inside them:", file=sys.stderr)
        for cyc in p.cycles:
            print("  " + " -> ".join(cyc), file=sys.stderr)
    if not p.ready:
        print(f"Nothing actionable ({p.summary()}).")
        for b in p.blocked[:10]:
            print(f"  {b.item}: {b.reason} — {b.detail}")
        return NOTHING
    print(f"Ready ({p.summary()}):")
    for it in p.ready:
        print(f"  {it.id}  {it.title}")
        if it.globs:
            print(f"      writes: {', '.join(it.globs)}")
    if len(p.ready) > 1:
        print("\nThese are independent — run them in parallel worktrees.")
    for b in p.blocked[:6]:
        print(f"  (blocked) {b.item}: {b.reason} — {b.detail}")
    return OK


def cmd_claim(a, c: Ctx) -> int:
    """Acquire a lease and (optionally) create the worktree. Exit 3 if refused."""
    try:
        lz = L.acquire(
            c.log, c.cfg, a.id, globs=_csv(a.globs) or None, note=a.note or "", force=a.force
        )
    except L.LeaseError as exc:
        print(str(exc), file=sys.stderr)
        if exc.alternatives:
            print("\nYou could take instead: " + ", ".join(exc.alternatives), file=sys.stderr)
        return REFUSED
    wt = None
    if c.cfg.worktree.enabled and not a.no_worktree:
        try:
            wt = W.create(c.repo, c.cfg, a.id)
            c.log.append(
                "worktree.created",
                a.id,
                {"path": str(wt.path), "branch": wt.branch, "base": wt.base},
            )
            L.acquire(
                c.log,
                c.cfg,
                a.id,
                worktree=str(wt.path),
                branch=wt.branch,
                globs=_csv(a.globs) or None,
                force=True,
            )
        except W.GitError as exc:
            print(f"lease held, but worktree creation failed: {exc}", file=sys.stderr)
            return FAIL
    c.log.append("item.started", a.id, {})
    msg = f"claimed {a.id} (lease {c.cfg.lease.ttl_s}s, renew every {c.cfg.lease.heartbeat_s}s)"
    if wt:
        msg += f"\n  worktree: {wt.path}\n  branch:   {wt.branch} (from {wt.base})\n  cd there and work."
    c.out(
        msg,
        {
            "item": a.id,
            "holder": lz.holder,
            "worktree": str(wt.path) if wt else "",
            "branch": wt.branch if wt else "",
        },
    )
    return OK


def cmd_heartbeat(a, c: Ctx) -> int:
    ok = L.renew(c.log, a.id)
    c.out(f"{'renewed' if ok else 'no lease held'} {a.id}", {"renewed": ok})
    return OK if ok else NOTHING


def cmd_release(a, c: Ctx) -> int:
    ok = L.release(c.log, a.id, note=a.note or "")
    c.out(f"{'released' if ok else 'no lease on'} {a.id}", {"released": ok})
    return OK if ok else NOTHING


def cmd_gate(a, c: Ctx) -> int:
    st = c.state()
    if a.gate_cmd == "status":
        try:
            s = G.status(st, c.cfg, a.id)
        except KeyError:
            print(f"no such item {a.id!r}", file=sys.stderr)
            return FAIL
        if c.json:
            print(json.dumps(_plain(s), indent=2, default=str))
            return OK
        print(f"{a.id}: {'COMPLETE' if s.complete else 'next = ' + (s.current or '—')}")
        print(s.render())
        gd = c.gates.get(s.current)
        if gd and gd.prompt:
            print(f"\n{gd.title}: {gd.prompt}")
        if s.unavailable:
            print(
                f"\n  NOTE: {', '.join(s.unavailable)} did not run. "
                f"That is a gap in coverage, not a pass."
            )
        return OK

    if a.gate not in c.gates:
        print(f"unknown gate {a.gate!r}; known: {', '.join(sorted(c.gates))}", file=sys.stderr)
        return FAIL
    gdef = c.gates[a.gate]
    it = st.items.get(a.id)
    if not it:
        print(f"no such item {a.id!r}", file=sys.stderr)
        return FAIL

    if a.gate_cmd == "run":
        if not gdef.is_command_gate:
            print(
                f"gate {a.gate!r} is an AGENT gate — Orchard cannot perform it.\n\n"
                f"{gdef.prompt}\n\n"
                f"When done: orchard gate record {a.id} {a.gate} "
                f"--outcome passed --evidence '<what you ran / what it said>'",
                file=sys.stderr,
            )
            return NOTHING
        cwd = Path(it.worktree) if (gdef.cwd == "worktree" and it.worktree) else c.repo
        c.log.append("gate.started", a.id, {"gate": a.gate})
        outcome, ev = G.run_command_gate(gdef, cwd)
        reason = ev.get("reason", "")
        if not reason and outcome != "passed":
            # Synthesise the reason from what actually happened. The requirement that a
            # non-pass carries a reason exists so a human can act on it; for a command
            # gate the exit code and the output tail ARE that reason, and demanding the
            # caller retype them would only mean the most common outcome of all -- a
            # failing suite -- could not be recorded at all.
            reason = f"`{gdef.command}` exited {ev.get('exit')}" + (
                f": {ev.get('tail', '').strip().splitlines()[-1][:160]}"
                if ev.get("tail", "").strip()
                else ""
            )
        G.record(c.log, c.cfg, a.id, a.gate, outcome, reason=reason, evidence=ev, gates=c.gates)
        tail = ev.get("tail", "")
        if not c.json and tail:
            print(tail[-1200:])
        c.out(
            f"{a.gate}: {outcome.upper()}"
            + (f" — {ev.get('reason', '')}" if ev.get("reason") else ""),
            {"gate": a.gate, "outcome": outcome, "evidence": ev},
        )
        return {
            "passed": OK,
            "skipped": OK,
            "failed": FAIL,
            "unavailable": NOTHING,
            "partial": NOTHING,
        }[outcome]

    if a.gate_cmd in ("record", "skip"):
        outcome = "skipped" if a.gate_cmd == "skip" else a.outcome
        ev: dict[str, Any] = {}
        if a.evidence:
            ev["note"] = a.evidence
        if a.command:
            ev["command"] = a.command
        if a.exit_code is not None:
            ev["exit"] = a.exit_code
        if a.model:
            ev["model"] = a.model
        if a.output_file:
            try:
                txt = Path(a.output_file).read_text("utf-8", errors="replace")
            except OSError as exc:
                print(f"--output-file unreadable: {exc}", file=sys.stderr)
                return FAIL
            ev["output_digest"] = G.digest(txt)
            ev["output_bytes"] = len(txt)
            ev["tail"] = txt[-2000:]
        try:
            G.record(
                c.log,
                c.cfg,
                a.id,
                a.gate,
                outcome,
                reason=a.reason or "",
                evidence=ev or None,
                gates=c.gates,
                by=a.model or "",
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return FAIL
        c.out(f"{a.id}.{a.gate} = {outcome}", {"gate": a.gate, "outcome": outcome})
        return OK
    return FAIL


def cmd_complete(a, c: Ctx) -> int:
    """Finish an item, refusing on an incomplete pipeline unless forced.

    Every unmet condition is collected and reported TOGETHER. Reporting only the first
    one turns a single refusal into a round-trip per problem, and an agent that has to
    guess how many more are coming tends to reach for --force. A refusal is only
    actionable if it is complete.
    """
    st = c.state()
    it = st.items.get(a.id)
    if not it:
        print(f"no such item {a.id!r}", file=sys.stderr)
        return FAIL
    s = G.status(st, c.cfg, a.id)
    required = set(c.cfg.gates.required)
    blockers: list[str] = []

    missing = [g for g in s.pipeline if g in required and it.gate_outcome(g) != "passed"]
    if missing:
        blockers.append(
            f"required gate(s) not passed: {', '.join(missing)} "
            f"(current outcome: "
            + ", ".join(f"{g}={it.gate_outcome(g) or 'not run'}" for g in missing)
            + ")"
        )
    if it.kind == "phase":
        open_tasks = [t.id for t in st.tasks(it.id) if t.state != "done"]
        if open_tasks:
            blockers.append(
                f"{len(open_tasks)} task(s) in this phase are unfinished: "
                f"{', '.join(open_tasks[:8])}"
            )
    if c.cfg.gates.unavailable_is_failure and s.unavailable:
        blockers.append(
            f"gate(s) could not run: {', '.join(s.unavailable)} "
            f"([gates].unavailable_is_failure is on, so a gap blocks like a failure)"
        )
    ok, why = G.reviewer_independence(st, c.cfg, a.id, a.model or "")
    if c.cfg.agent.reviewer_family_must_differ and not ok and it.kind == "task":
        blockers.append(f"reviewer independence not satisfied: {why}")

    if blockers and not a.force:
        print(f"cannot complete {a.id} — {len(blockers)} unmet condition(s):", file=sys.stderr)
        for b in blockers:
            print(f"  - {b}", file=sys.stderr)
        print(
            f"\n`orchard gate status {a.id}` shows the pipeline. --force overrides, "
            f"and the override is recorded.",
            file=sys.stderr,
        )
        return REFUSED
    if s.unavailable and not c.json:
        print(
            f"NOTE: {', '.join(s.unavailable)} never ran — recorded as a coverage gap, "
            f"not as a pass."
        )
    c.log.append(
        "item.completed",
        a.id,
        {
            "sha": a.sha or "",
            "kind": it.kind,
            "forced": bool(blockers and a.force),
            "overridden": blockers if a.force else [],
        },
    )
    L.release(c.log, a.id, note="completed")
    c.out(
        f"{a.id} completed"
        + (f" as {a.sha}" if a.sha else "")
        + (f" [FORCED over {len(blockers)} unmet condition(s)]" if blockers else ""),
        {"id": a.id, "sha": a.sha or "", "independence": why, "forced": bool(blockers and a.force)},
    )
    return OK


def cmd_block(a, c: Ctx) -> int:
    c.log.append("item.blocked", a.id, {"reason": a.reason})
    c.out(f"{a.id} blocked: {a.reason}", {"id": a.id})
    return OK


def cmd_merge(a, c: Ctx) -> int:
    st = c.state()
    it = st.items.get(a.id)
    if not it or not it.worktree:
        print(f"{a.id} has no worktree to merge", file=sys.stderr)
        return NOTHING
    wt = W.Worktree(
        item=a.id,
        path=Path(it.worktree),
        branch=it.branch,
        base=c.cfg.worktree.base_ref or W.default_branch(c.repo),
    )
    d = W.dirty(wt)
    if d and not a.allow_dirty:
        # Listed, not just counted: half the time these are build artefacts the project
        # forgot to gitignore, and half the time they are a source file the agent never
        # `git add`-ed -- which would be silently dropped from the merge. The operator
        # can only tell which by seeing the names.
        print(
            f"{len(d)} uncommitted file(s) in {wt.path} would NOT be included in the merge:",
            file=sys.stderr,
        )
        for entry in d[:MAX_LISTED_FILES]:
            print(f"  {entry}", file=sys.stderr)
        if len(d) > MAX_LISTED_FILES:
            print(f"  ... and {len(d) - MAX_LISTED_FILES} more", file=sys.stderr)
        print(
            "\nCommit them, add them to .gitignore if they are build output, or pass "
            "--allow-dirty to merge without them.",
            file=sys.stderr,
        )
        return REFUSED
    sha = W.head_sha(wt.path)
    r = W.merge(c.repo, c.cfg, wt, message=a.message or f"merge {a.id}: {it.title}")
    if not r.ok:
        print(r.err or r.out, file=sys.stderr)
        return REFUSED if r.code == GIT_REFUSED else FAIL
    c.log.append("worktree.merged", a.id, {"sha": sha, "branch": wt.branch})
    G.record(
        c.log,
        c.cfg,
        a.id,
        "merge",
        "passed",
        evidence={"sha": sha, "branch": wt.branch},
        gates=c.gates,
    )
    if c.cfg.worktree.remove_on_merge and not a.keep:
        rr = W.remove(c.repo, c.cfg, wt)
        if rr.ok:
            c.log.append("worktree.removed", a.id, {"path": str(wt.path)})
        else:
            print(rr.err, file=sys.stderr)
    c.out(f"merged {a.id} ({sha[:8]}) into {wt.base}", {"id": a.id, "sha": sha})
    return OK


def cmd_brief(a, c: Ctx) -> int:
    st = c.store.ensure(c.log)
    p = plan(st, c.cfg, phase=a.phase or "", agent=c.cfg.agent.id or c.log.agent_id)
    item = a.item or ""
    if not item and p.ready:
        item = p.ready[0].id
    q = ""
    if item and item in st.items:
        it = st.items[item]
        q = f"{it.title} {it.body} {' '.join(it.tags)}"
    lessons = c.store.search("lessons", q, c.cfg.session.brief_lesson_count) if q else []
    rules = ""
    for cand in ("AGENTS.md", "CLAUDE.md", ".orchard/RULES.md"):
        p_ = c.repo / cand
        if p_.is_file():
            rules = f"See `{cand}` (loaded separately by your agent)."
            break
    rec = L.scan(c.log, c.cfg, c.repo) if a.check_recovery else []
    text = render.brief(
        st,
        c.cfg,
        p,
        item=item,
        lessons=lessons,
        rules=rules,
        recovery=[r for r in rec if r.salvageable],
    )
    if c.json:
        print(
            json.dumps(
                {
                    "brief": text,
                    "item": item,
                    "ready": [i.id for i in p.ready],
                    "approx_tokens": len(text) // 4,
                },
                indent=2,
            )
        )
    else:
        print(text)
    return OK


def cmd_lesson(a, c: Ctx) -> int:
    if a.lesson_cmd == "add":
        lid = a.id or _auto_id("L", a.title, a.rule or "")
        c.log.append(
            "lesson.recorded",
            lid,
            {
                "title": a.title,
                "rule": a.rule or "",
                "why": a.why or "",
                "how": a.how or "",
                "tags": _csv(a.tags),
                "seen_in": _csv(a.seen_in),
                "supersedes": _csv(a.supersedes),
            },
        )
        c.out(f"lesson {lid} recorded", {"id": lid})
        return OK
    if a.lesson_cmd == "search":
        c.store.ensure(c.log)
        hits = c.store.search(
            "lessons", a.query, a.limit if a.limit is not None else c.cfg.lessons.max_results
        )
        if c.json:
            print(json.dumps(hits, indent=2, default=str))
            return OK if hits else NOTHING
        if not hits:
            print("no matching lessons")
            return NOTHING
        for h in hits:
            print(f"- {h['title']}\n    {(h.get('rule') or '')[: c.cfg.lessons.snippet_chars]}")
        return OK
    return FAIL


def cmd_research(a, c: Ctx) -> int:
    rid = a.id or _auto_id("R", a.question, a.claim or "")
    if a.verdict not in ("CONFIRMED", "REFUTED", "THEORETICAL"):
        print(
            "verdict must be CONFIRMED, REFUTED or THEORETICAL. A note with no "
            "verdict is a literature summary, not research.",
            file=sys.stderr,
        )
        return FAIL
    if a.verdict in ("CONFIRMED", "REFUTED") and not (a.probe or a.probe_output):
        print(
            f"{a.verdict} requires a --probe (and ideally --probe-output): a verdict "
            f"with no probe behind it is an opinion. Use THEORETICAL and say why no "
            f"probe was possible.",
            file=sys.stderr,
        )
        return FAIL
    c.log.append(
        "research.recorded",
        rid,
        {
            "question": a.question,
            "claim": a.claim or "",
            "mechanism": a.mechanism or "",
            "falsifier": a.falsifier or "",
            "probe": a.probe or "",
            "probe_output": a.probe_output or "",
            "verdict": a.verdict,
            "sources": _csv(a.sources),
            "budget": a.budget or "",
            "item": a.item or "",
        },
    )
    c.out(f"research {rid} recorded ({a.verdict})", {"id": rid, "verdict": a.verdict})
    return OK


def cmd_bug(a, c: Ctx) -> int:
    if a.bug_cmd == "found":
        bid = a.id or _auto_id("B", a.summary, a.item or "")
        c.log.append("bug.found", bid, {"item": a.item or "", "summary": a.summary})
        c.out(f"bug {bid} recorded", {"id": bid})
        return OK
    if not a.regression_test and c.cfg.lessons.require_regression_test:
        print(
            "a bug may not be closed without --regression-test naming the test that "
            "would catch it again. Write the test, watch it FAIL against the "
            "unfixed code, then close.",
            file=sys.stderr,
        )
        return FAIL
    c.log.append(
        "bug.fixed", a.id, {"regression_test": a.regression_test or "", "lesson": a.lesson or ""}
    )
    if c.cfg.lessons.auto_capture_on_bug and a.lesson_title:
        c.log.append(
            "lesson.recorded",
            f"L-{a.id}",
            {
                "title": a.lesson_title,
                "rule": a.lesson_rule or "",
                "seen_in": [a.id],
                "tags": ["bug"],
            },
        )
    c.out(f"bug {a.id} closed (regression: {a.regression_test})", {"id": a.id})
    return OK


def cmd_session(a, c: Ctx) -> int:
    if a.session_cmd == "start":
        sid = session.start(c.log, c.cfg, model=a.model or "", agent_tool=a.tool or "")
        c.out(sid, {"session": sid})
        return OK
    if a.session_cmd == "prompt":
        text = a.text if a.text is not None else sys.stdin.read()
        n = session.prompt(c.log, c.cfg, a.session, text, item=a.item or "")
        c.out(f"recorded ({n} redaction(s))", {"redactions": n})
        return OK
    if a.session_cmd == "note":
        session.note(c.log, c.cfg, a.session, a.text or sys.stdin.read(), item=a.item or "")
        c.out("noted", {})
        return OK
    if a.session_cmd == "end":
        session.end(c.log, a.session, summary=a.summary or "")
        c.out("ended", {})
        return OK
    return FAIL


def cmd_replay(a, c: Ctx) -> int:
    st = c.state()
    steps = session.replay(c.log.read_all())
    if a.verify:
        problems = session.verify(st, c.repo, c.cfg)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
        if problems:
            print(f"{len(problems)} recorded commit(s) no longer resolve.", file=sys.stderr)
    if a.out:
        files = session.bundle(st, steps, Path(a.out), c.cfg, project=c.repo.name)
        c.out("wrote:\n" + "\n".join(f"  {f}" for f in files), {"files": [str(f) for f in files]})
        return OK
    print(session.render_reconstruction(st, steps, project=c.repo.name))
    return OK


def cmd_recover(a, c: Ctx) -> int:
    found = L.sweep(c.log, c.cfg, c.repo, apply=a.apply)
    found = [r for r in found if not a.item or r.item == a.item]
    if c.json:
        print(json.dumps([_plain(r) for r in found], indent=2, default=str))
        return OK if found else NOTHING
    if not found:
        print("Nothing to recover — no expired leases, no orphan worktrees.")
        return NOTHING
    salv = [r for r in found if r.salvageable]
    print(f"{len(found)} recoverable situation(s); {len(salv)} may contain work:\n")
    for r in found:
        flag = "!! " if r.salvageable else "   "
        print(f"{flag}{r.item}  [{r.kind}]  was: {r.holder}")
        if r.worktree:
            print(f"     worktree {r.worktree}")
        print(f"     {r.advice}\n")
    if salv:
        print("Worktrees marked !! are NOT touched automatically. Inspect, salvage, then release.")
    return OK


def cmd_doctor(a, c: Ctx) -> int:
    problems: list[str] = []
    notes: list[str] = []
    problems += c.log.verify()
    if not (c.repo / ".orchard").exists():
        problems.append("no .orchard directory — run `orchard init`")
    if c.store.stale(c.log):
        notes.append("index is stale; it rebuilds automatically on next read")
    st = c.state()
    p = plan(st, c.cfg, agent=c.log.agent_id)
    for cyc in p.cycles:
        problems.append("dependency cycle: " + " -> ".join(cyc))
    for it in st.items.values():
        for dep in it.needs:
            if dep not in st.items:
                problems.append(f"{it.id} needs unknown item {dep!r}")
    rec = L.scan(c.log, c.cfg, c.repo)
    for r in rec:
        (problems if r.salvageable else notes).append(f"{r.kind}: {r.item} — {r.advice}")
    ghosts = [
        w.get("worktree", "")
        for w in W.list_worktrees(c.repo)
        if c.cfg.worktree.branch_prefix.rstrip("/") in w.get("branch", "")
    ]
    known = {it.worktree for it in st.items.values() if it.worktree}
    for g in ghosts:
        if g and g not in known:
            notes.append(f"worktree {g} exists but no item claims it")
    if c.json:
        print(
            json.dumps(
                {
                    "problems": problems,
                    "notes": notes,
                    "events": st.event_count,
                    "items": len(st.items),
                },
                indent=2,
            )
        )
        return FAIL if problems else OK
    print(
        f"events {st.event_count} · items {len(st.items)} · agent {c.log.agent_id} · repo {c.repo}"
    )
    print(
        f"index: {'stale (auto-rebuilds)' if c.store.stale(c.log) else 'current'} · "
        f"fts5: {'yes' if c.store.fts else 'no (LIKE fallback)'}"
    )
    for n in notes:
        print(f"  note: {n}")
    for pr in problems:
        print(f"  PROBLEM: {pr}")
    print("\nHealthy." if not problems else f"\n{len(problems)} problem(s).")
    return FAIL if problems else OK


def cmd_rebuild(a, c: Ctx) -> int:
    t0 = time.time()
    st = c.store.rebuild(c.log)
    c.out(
        f"rebuilt index from {st.event_count} events in {time.time() - t0:.2f}s "
        f"({len(st.items)} items, {len(st.lessons)} lessons)",
        {"events": st.event_count, "items": len(st.items)},
    )
    return OK


def cmd_render(a, c: Ctx) -> int:
    st = c.store.ensure(c.log)
    files = render.write_views(c.repo, st, c.cfg, subdir=a.out)
    c.out("\n".join(str(f) for f in files), {"files": [str(f) for f in files]})
    return OK


def cmd_board(a, c: Ctx) -> int:
    print(render.board(c.state(), c.cfg, phase=a.phase or ""))
    return OK


def cmd_show(a, c: Ctx) -> int:
    st = c.state()
    it = st.items.get(a.id)
    if not it:
        print(f"no such item {a.id!r}", file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(_plain(it), indent=2, default=str))
        return OK
    print(f"{it.id} [{it.kind}] {it.title}\n  state {it.state}")
    if it.needs:
        print(f"  needs {', '.join(it.needs)}")
    if it.globs:
        print(f"  globs {', '.join(it.globs)}")
    if it.lease:
        print(f"  lease {it.lease.holder} ({it.lease.remaining_s(time.time()):.0f}s left)")
    if it.worktree:
        print(f"  worktree {it.worktree} [{it.branch}]")
    if it.body:
        print(f"\n{it.body}\n")
    print(G.status(st, c.cfg, a.id).render())
    return OK


def cmd_config(a, c: Ctx) -> int:
    if a.set:
        return _config_set(a, c)
    if a.append_toml:
        # Validated BEFORE writing: an agent composing TOML gets a parse error back as
        # a readable message instead of leaving the project with a config that no
        # later command can load.
        import tomllib

        try:
            tomllib.loads(a.append_toml)
        except tomllib.TOMLDecodeError as exc:
            print(f"not valid TOML: {exc}", file=sys.stderr)
            return FAIL
        path = c.repo / ".orchard" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = prev.rstrip() + "\n\n" + a.append_toml.strip() + "\n"
        try:
            Config.load(c.repo, env={}) and tomllib.loads(merged)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            print(f"appending this would break the config: {exc}", file=sys.stderr)
            return FAIL
        path.write_text(merged, "utf-8")
        c.out(f"appended to {path}", {"path": str(path)})
        return OK
    rows = c.cfg.explain()
    if c.json:
        print(
            json.dumps(
                [{"key": k, "value": v, "source": s, "doc": d} for k, v, s, d in rows],
                indent=2,
                default=str,
            )
        )
        return OK
    for k, v, s, d in rows:
        if a.filter and a.filter not in k:
            continue
        print(f"{k} = {v!r}   [{s}]")
        if a.explain and d:
            for line in _wrap(d, 76):
                print(f"    {line}")
    return OK


def _config_set(a, c: Ctx) -> int:
    """`orchard config --set <section>.<key> <value>` — edit one key in place.

    Exists because appending is not always possible: TOML forbids a duplicate table, so
    once a section is present the documented "append a block" path fails. Editing in
    place also preserves the surrounding comments, which for this file carry most of
    the reasoning.
    """
    import tomllib

    dotted, value = a.set, a.value
    if "." not in dotted:
        print("--set takes <section>.<key>, e.g. gate.unit_tests.command", file=sys.stderr)
        return FAIL
    section, _, key = dotted.rpartition(".")
    path = c.repo / ".orchard" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text("utf-8") if path.exists() else ""

    literal = value
    if not re.fullmatch(r"(true|false|-?\d+(\.\d+)?|\[.*\]|\{.*\})", value.strip()):
        literal = json.dumps(value)  # quote + escape as a TOML basic string

    lines = text.splitlines()
    header = f"[{section}]"
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
    except StopIteration:
        block = ["", header, f"{key} = {literal}"]
        lines += block
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
            len(lines),
        )
        for i in range(start + 1, end):
            stripped = lines[i].lstrip()
            if stripped.startswith(("#", ";")):
                continue
            if stripped.split("=")[0].strip() == key:
                lines[i] = f"{key} = {literal}"
                break
        else:
            lines.insert(end, f"{key} = {literal}")
    new = "\n".join(lines).rstrip() + "\n"
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        print(f"that edit would break the config: {exc}", file=sys.stderr)
        return FAIL
    path.write_text(new, "utf-8")
    c.out(f"{dotted} = {literal}", {"key": dotted, "value": value, "path": str(path)})
    return OK


def cmd_cadence(a, c: Ctx) -> int:
    """Which periodic passes are due? Derived from the log, so there is no state file."""
    st = c.state()
    done_tasks = sum(1 for i in st.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in st.items.values() if i.kind == "phase" and i.state == "done")
    due = []
    for name, every, unit, count in (
        ("integration_tests", c.cfg.cadence.integration_tests_every_tasks, "tasks", done_tasks),
        ("dedupe_sweep", c.cfg.cadence.dedupe_sweep_every_tasks, "tasks", done_tasks),
        (
            "architecture_review",
            c.cfg.cadence.architecture_review_every_phases,
            "phases",
            done_phases,
        ),
        ("mutation_tests", c.cfg.cadence.mutation_tests_every_phases, "phases", done_phases),
        ("lessons_pass", c.cfg.cadence.lessons_pass_every_phases, "phases", done_phases),
    ):
        runs = st.cadences.get(name, [])
        at_last = int(runs[-1].get("result", "0") or 0) if runs else 0
        since = count - at_last
        if every > 0 and since >= every:
            due.append({"cadence": name, "since": since, "every": every, "unit": unit})
    due += _lessons_cadence(st, c.cfg)
    if a.ran:
        if a.ran == "lessons_compression":
            live = [x for x in st.lessons.values() if not x.superseded_by]
            result = json.dumps(
                {"bytes": sum(len(x.text().encode("utf-8")) for x in live), "entries": len(live)}
            )
        else:
            result = str(
                done_tasks if a.ran in ("integration_tests", "dedupe_sweep") else done_phases
            )
        c.log.append("cadence.ran", a.ran, {"result": result, "evidence": {"note": a.note or ""}})
        c.out(f"recorded cadence run: {a.ran}", {"cadence": a.ran})
        return OK
    if c.json:
        print(json.dumps(due, indent=2))
        return OK if due else NOTHING
    if not due:
        print(f"No cadence due ({done_tasks} tasks, {done_phases} phases completed).")
        return NOTHING
    for d in due:
        print(f"DUE: {d['cadence']} — {d['since']} {d['unit']} since last (every {d['every']})")
    print("\nRecord one with: orchard cadence --ran <name>")
    return OK


def _diff_for(c: Ctx, item_id: str, base: str = "") -> tuple[str, str]:
    """(diff, how) for an item: its worktree branch vs base, else the dirty tree.

    Returns the branch diff when the item has a worktree, because by review time the
    work is usually committed there and `git diff` alone would be empty -- and an empty
    diff is the commonest way a review passes having examined nothing.
    """
    st = c.state()
    it = st.items.get(item_id)
    base = base or c.cfg.worktree.base_ref or W.default_branch(c.repo)
    if it and it.worktree and Path(it.worktree).exists():
        wt = Path(it.worktree)
        merge_base = W.git(wt, "merge-base", base, "HEAD").out or base
        committed = W.git(wt, "diff", f"{merge_base}..HEAD").out
        dirty = W.git(wt, "diff").out
        both = "\n".join(x for x in (committed, dirty) if x.strip())
        if both.strip():
            return both, f"{base}..HEAD (+ uncommitted) in {wt}"
    dirty = W.git(c.repo, "diff", "HEAD").out
    return dirty, f"uncommitted changes in {c.repo}"


def cmd_reviewers(a, c: Ctx) -> int:
    from . import reviewer as R

    if a.reviewers_cmd == "detect":
        found = R.detect()
        if not found:
            print(
                "No local OpenAI-compatible endpoint answered on any well-known port.\n"
                "Checked: " + ", ".join(u for u, _ in R.WELL_KNOWN_ENDPOINTS),
                file=sys.stderr,
            )
            return NOTHING
        blocks = []
        for url, label, models in found:
            for m in models:
                fam = R.family_of(m)
                print(f"  {url}  [{label}]\n      model  {m}\n      family {fam}")
                blocks.append(
                    f'\n[[reviewer]]\nname = "{m.split("/")[-1].lower()}"\n'
                    f'base_url = "{url}"\nmodel = "{m}"\nfamily = "{fam}"\n'
                    f'gates = ["critic"]\n'
                )
        if a.write:
            cfg_path = c.repo / ".orchard" / "config.toml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            prev = cfg_path.read_text("utf-8") if cfg_path.exists() else ""
            cfg_path.write_text(prev.rstrip() + "\n" + "".join(blocks), "utf-8")
            print(f"\nappended {len(blocks)} reviewer block(s) to {cfg_path}")
        else:
            print("\nAdd to .orchard/config.toml (or re-run with --write):")
            print("".join(blocks))
        return OK

    revs = R.load_reviewers(c.repo)
    if a.reviewers_cmd == "list":
        if not revs:
            print("No reviewers configured. Run `orchard reviewers detect --write`.")
            return NOTHING
        for r in revs:
            print(
                f"  {r.name:<22} {r.resolved_family():<12} gates={','.join(r.gates)} "
                f"{'' if r.enabled else '(disabled) '}{r.base_url} [{r.model}]"
            )
        return OK

    if a.reviewers_cmd == "test":
        targets = [r for r in revs if not a.name or r.name == a.name]
        if not targets:
            print(f"no reviewer named {a.name!r}; `orchard reviewers list`", file=sys.stderr)
            return FAIL
        worst = OK
        for r in targets:
            res = R.review(
                r,
                "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                "@@ -1,3 +1,3 @@\n def f(items):\n-    return sum(items) / len(items)\n"
                "+    return sum(items) / len(items) if items else 0\n",
                intent="guard the empty-list case in the mean helper",
            )
            print(
                f"  {r.name}: {res.label} ({res.coverage()}, {res.elapsed_s:.1f}s)"
                + (f" — {res.reason}" if res.reason else "")
            )
            for f in res.findings[:3]:
                print(f"      [{f.severity}] {f.title[:90]}")
            worst = max(worst, 0 if res.status == R.REVIEWED else res.status)
        return worst
    return FAIL


def cmd_review(a, c: Ctx) -> int:
    """Run every reviewer configured for a gate, and record the outcome."""
    from . import reviewer as R

    revs = R.reviewers_for(R.load_reviewers(c.repo), a.gate)
    if not revs:
        print(
            f"No reviewer is configured for gate {a.gate!r}. "
            f"`orchard reviewers detect --write` finds local models.\n"
            f"Recording UNAVAILABLE — which is NOT a pass.",
            file=sys.stderr,
        )
        if a.id:
            G.record(
                c.log,
                c.cfg,
                a.id,
                a.gate,
                "unavailable",
                reason=f"no reviewer configured for {a.gate}",
                gates=c.gates,
            )
        return NOTHING

    diff, how = _diff_for(c, a.id, a.base)
    if not diff.strip():
        print(f"Empty diff ({how}) — nothing to review. Recording UNAVAILABLE.", file=sys.stderr)
        if a.id:
            G.record(
                c.log,
                c.cfg,
                a.id,
                a.gate,
                "unavailable",
                reason=f"empty diff ({how})",
                gates=c.gates,
            )
        return UNAVAILABLE_EXIT

    st = c.state()
    it = st.items.get(a.id)
    intent = a.intent or (f"{it.title}. {it.body}".strip() if it else "")
    if not intent:
        print(
            "--intent is required: the reviewer flags where the diff and the stated "
            "intent disagree, so without it there is nothing to disagree with.",
            file=sys.stderr,
        )
        return FAIL

    results = []
    for r in revs:
        print(
            f"→ {r.name} ({r.resolved_family()}) reviewing {len(diff)} chars from {how}", flush=True
        )
        res = R.review(r, diff, intent, context=a.context or "")
        results.append(res)
        print(
            f"  {res.label}: {len(res.findings)} finding(s), {res.coverage()}, "
            f"{res.elapsed_s:.1f}s" + (f" — {res.reason}" if res.reason else ""),
            flush=True,
        )
        for f in res.findings:
            print(f"\n  [{f.severity}] {f.location or f.title}")
            for line in f.detail.splitlines():
                if line.strip():
                    print(f"      {line.strip()}")

    best = min(results, key=lambda r: r.status)
    if a.id:
        outcome = {
            R.REVIEWED: ("failed" if best.findings else "passed"),
            R.PARTIAL: "partial",
            R.UNAVAILABLE: "unavailable",
            R.ERROR: "unavailable",
        }[best.status]
        G.record(
            c.log,
            c.cfg,
            a.id,
            a.gate,
            outcome,
            reason=best.reason or (f"{len(best.findings)} finding(s)" if best.findings else ""),
            evidence={**best.evidence(), "diff_source": how, "diff_chars": len(diff)},
            gates=c.gates,
            by=best.model,
        )
        print(
            f"\nrecorded {a.id}.{a.gate} = {outcome} (reviewer {best.reviewer}, "
            f"family {best.family})"
        )
    return {R.REVIEWED: OK, R.PARTIAL: REFUSED, R.UNAVAILABLE: NOTHING, R.ERROR: FAIL}[best.status]


def cmd_hooks(a, c: Ctx) -> int:
    from . import enforce as E

    if a.hooks_cmd == "install":
        msg = E.install(c.repo, force=a.force)
        c.out(msg, {"message": msg})
        return FAIL if msg.startswith("REFUSED") else OK
    if a.hooks_cmd == "uninstall":
        msg = E.uninstall(c.repo)
        c.out(msg, {"message": msg})
        return OK
    if a.hooks_cmd == "status":
        on = E.installed(c.repo)
        mode = c.cfg.enforce.commit_without_lease
        c.out(
            f"pre-commit hook: {'installed' if on else 'NOT installed'}\n"
            f"policy [enforce].commit_without_lease = {mode!r}"
            + (
                "\n\nNOTE: the hook is installed but the policy is 'warn', so it "
                "reports and allows. Set it to 'block' to refuse."
                if on and mode == "warn"
                else ""
            )
            + (
                "\n\nNOTE: the policy is 'block' but NO HOOK IS INSTALLED, so nothing "
                "enforces it. Run `orchard hooks install`."
                if not on and mode == "block"
                else ""
            ),
            {"installed": on, "policy": mode},
        )
        return OK if on or mode == "off" else NOTHING
    if a.hooks_cmd == "check-commit":
        code, msg = E.check_commit(c.repo, c.cfg)
        if msg:
            print(msg, file=sys.stderr)
        if code == 0 and c.cfg.enforce.require_item_trailer:
            tcode, tmsg = E.check_item_trailer(c.repo)
            if tmsg:
                print(tmsg, file=sys.stderr)
            return tcode
        return code
    return FAIL


def cmd_adopt(a, c: Ctx) -> int:
    from .adopt import AGENT_TARGETS, adopt

    agents = _csv(a.agents) or list(AGENT_TARGETS)
    try:
        actions = adopt(
            c.repo, agents, docs_dir=a.docs, install_hooks=c.cfg.enforce.install_hooks_on_setup
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    cmd_init(a, c)
    c.out(
        "\n".join(f"  {x}" for x in actions) + f"\n\nOrchard adopted for: {', '.join(agents)}.\n"
        f"  1. set your test command in .orchard/gates.toml\n"
        f"  2. orchard phase add P1 --title '...'\n"
        f"  3. tell your agent: implement phase P1",
        {"actions": actions, "agents": agents},
    )
    return OK


def _lessons_cadence(st, cfg) -> list[dict[str, Any]]:
    """Is a lessons-compression pass due?

    Measured as GROWTH since the last recorded pass, not as an absolute size. An
    absolute threshold fires forever once crossed -- including immediately after a pass
    that just correctly compressed the corpus -- which trains everyone to ignore it.
    Growth goes quiet when the work is done, which is the only behaviour that keeps a
    cadence trigger credible.

    Both halves must clear: total bytes AND bytes-per-entry. Dividing by entry count
    alone would fire on a MERGE (fewer entries, same bytes => per-entry rises), i.e. on
    exactly the action the cadence exists to produce.
    """
    live = [x for x in st.lessons.values() if not x.superseded_by]
    if len(live) < cfg.lessons.cadence_min_entries:
        return []
    runs = st.cadences.get("lessons_compression", [])
    now_bytes = sum(len(x.text().encode("utf-8")) for x in live)
    now_per = now_bytes / max(1, len(live))
    if not runs:
        return [
            {
                "cadence": "lessons_compression",
                "since": len(live),
                "every": 0,
                "unit": "entries (no baseline recorded yet)",
            }
        ]
    try:
        was = json.loads(runs[-1].get("result") or "{}")
        was_bytes, was_entries = float(was["bytes"]), int(was["entries"])
    except (ValueError, KeyError, TypeError):
        return []
    was_per = was_bytes / max(1, was_entries)
    grow_bytes = (now_bytes - was_bytes) / max(1.0, was_bytes) * 100
    grow_per = (now_per - was_per) / max(1e-9, was_per) * 100
    thresh = cfg.lessons.cadence_growth_pct
    if grow_bytes >= thresh and grow_per >= thresh:
        return [
            {
                "cadence": "lessons_compression",
                "since": round(grow_per, 1),
                "every": thresh,
                "unit": f"% growth per entry (total +{grow_bytes:.1f}%)",
            }
        ]
    return []


def cmd_mcp(a, c: Ctx) -> int:
    from .mcp_server import serve

    serve(c.repo)
    return OK


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width)


def _starter_config() -> str:
    """The single configuration file.

    One file, not two. An earlier version also wrote `.orchard/gates.toml` carrying a
    placeholder `unit_tests.command`, and because gates.toml wins over config.toml that
    placeholder silently overrode anything `orchard configure` wrote -- so the documented
    way to set the test command could not set the test command. Splitting gates into
    their own file is still supported for operators who want it; it is just not the
    default, because a default that creates two sources of truth will produce two
    sources of truth.
    """
    return """# Orchard configuration — everything in one file.
# `orchard config --explain` documents every knob. Only what you change needs to be
# here; everything else keeps its default.

# ---------------------------------------------------------------------------------
# THE ONE THING YOU MUST SET: how this project runs its tests.
# ---------------------------------------------------------------------------------
# [gate.unit_tests]
# command = "pytest -q"     # or "npm test" · "cargo test" · "go test ./..." · "make check"
#
# Set it with:   orchard config --set gate.unit_tests.command "pytest -q"
# Until it is set, the unit_tests gate reports UNAVAILABLE — which is honest, and
# blocks completion, rather than passing vacuously.
#
# Left COMMENTED on purpose: an empty table here would collide with the block that
# `orchard config --append-toml` writes, since TOML forbids a duplicate table, and the
# documented way to configure the project would fail on a fresh install.

# ---------------------------------------------------------------------------------
# A cross-family reviewer makes the `critic` gate real rather than self-reported.
# `orchard reviewers detect --write` finds a local model server and fills this in.
# ---------------------------------------------------------------------------------
# [[reviewer]]
# name     = "local"
# base_url = "http://127.0.0.1:11434/v1"
# model    = "qwen3:8b"
# family   = "alibaba"            # must differ from the authoring model's family
# gates    = ["critic"]
# api_key_env = "MY_API_KEY"      # the NAME of an env var, never the key itself

[lease]
ttl_s = 1800            # how long a claim survives without a heartbeat
heartbeat_s = 300

[worktree]
enabled = true
max_parallel = 4

[schedule]
max_parallel_tasks = 4

[enforce]
# "block" makes the pre-commit hook REFUSE a commit touching paths no lease of yours
# covers — the only layer of this workflow that does not rely on the agent agreeing.
# Starts at "warn" so adopting Orchard never breaks an existing repo on day one.
commit_without_lease = "warn"

[session]
brief_max_tokens = 1200
"""


# -- parser ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchard",
        description="A portable work-queue kernel for AI coding agents. "
        "The event log is the source of truth; everything else is derived.",
    )
    p.add_argument(
        "--repo", help="repository root (default: cwd, resolved to the primary checkout)"
    )
    p.add_argument("--agent", help="agent identity (default: host-pid). Shards the log.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    s = p.add_subparsers(dest="cmd", required=True)

    s.add_parser("init", help="create .orchard/ in this repository").set_defaults(fn=cmd_init)

    pa = s.add_parser("phase", help="add a phase")
    pa_s = pa.add_subparsers(dest="phase_cmd", required=True)
    pad = pa_s.add_parser("add")
    pad.add_argument("id")
    pad.add_argument("--title", default="")
    pad.add_argument("--needs")
    pad.add_argument("--globs")
    pad.add_argument("--tags")
    pad.add_argument("--body")
    pad.add_argument("--priority", type=int, default=100)
    pad.set_defaults(fn=cmd_phase_add)

    ta = s.add_parser("task", help="add a task")
    ta_s = ta.add_subparsers(dest="task_cmd", required=True)
    tad = ta_s.add_parser("add")
    tad.add_argument("id")
    tad.add_argument("--phase", default="")
    tad.add_argument("--title", default="")
    tad.add_argument("--needs")
    tad.add_argument("--globs")
    tad.add_argument("--tags")
    tad.add_argument("--body")
    tad.add_argument("--priority", type=int, default=100)
    tad.set_defaults(fn=cmd_task_add)

    up = s.add_parser("update", help="change an item's fields")
    up.add_argument("id")
    for f in ("title", "body", "needs", "globs", "tags"):
        up.add_argument(f"--{f}")
    up.add_argument("--priority", type=int)
    up.set_defaults(fn=cmd_item_update)

    nx = s.add_parser("next", help="what may start now (exit 2 = nothing actionable)")
    nx.add_argument("--phase", default="")
    nx.add_argument("--kind", default="task", choices=["task", "phase"])
    nx.set_defaults(fn=cmd_next)

    cl = s.add_parser("claim", help="lease an item + create its worktree (exit 3 = refused)")
    cl.add_argument("id")
    cl.add_argument("--globs")
    cl.add_argument("--note")
    cl.add_argument("--force", action="store_true")
    cl.add_argument("--no-worktree", action="store_true")
    cl.set_defaults(fn=cmd_claim)

    hb = s.add_parser("heartbeat", help="renew a lease")
    hb.add_argument("id")
    hb.set_defaults(fn=cmd_heartbeat)
    rl = s.add_parser("release", help="give up a lease")
    rl.add_argument("id")
    rl.add_argument("--note")
    rl.set_defaults(fn=cmd_release)

    g = s.add_parser("gate", help="run / record / inspect a gate")
    g_s = g.add_subparsers(dest="gate_cmd", required=True)
    gst = g_s.add_parser("status")
    gst.add_argument("id")
    gst.set_defaults(fn=cmd_gate, gate="")
    grun = g_s.add_parser("run")
    grun.add_argument("id")
    grun.add_argument("gate")
    grun.set_defaults(fn=cmd_gate)
    for name in ("record", "skip"):
        gr = g_s.add_parser(name)
        gr.add_argument("id")
        gr.add_argument("gate")
        gr.add_argument("--outcome", default="passed", choices=list(GATE_OUTCOMES))
        gr.add_argument("--reason", default="")
        gr.add_argument("--evidence", default="")
        gr.add_argument("--command", default="")
        gr.add_argument("--exit-code", type=int)
        gr.add_argument("--model", default="", help="reviewer model, for family independence")
        gr.add_argument("--output-file", default="")
        gr.set_defaults(fn=cmd_gate)

    cp = s.add_parser("complete", help="finish an item (exit 3 = gates not satisfied)")
    cp.add_argument("id")
    cp.add_argument("--sha", default="")
    cp.add_argument("--model", default="", help="the AUTHOR's model, for independence check")
    cp.add_argument("--force", action="store_true")
    cp.set_defaults(fn=cmd_complete)

    bl = s.add_parser("block")
    bl.add_argument("id")
    bl.add_argument("--reason", required=True)
    bl.set_defaults(fn=cmd_block)

    mg = s.add_parser("merge", help="merge an item's branch from the primary checkout")
    mg.add_argument("id")
    mg.add_argument("--message", default="")
    mg.add_argument("--keep", action="store_true")
    mg.add_argument("--allow-dirty", action="store_true")
    mg.set_defaults(fn=cmd_merge)

    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument("--check-recovery", action="store_true", default=True)
    br.set_defaults(fn=cmd_brief)

    ls = s.add_parser("lesson")
    ls_s = ls.add_subparsers(dest="lesson_cmd", required=True)
    la = ls_s.add_parser("add")
    la.add_argument("--id", default="")
    la.add_argument("--title", required=True)
    la.add_argument("--rule", default="")
    la.add_argument("--why", default="")
    la.add_argument("--how", default="")
    la.add_argument("--tags", default="")
    la.add_argument("--seen-in", default="")
    la.add_argument("--supersedes", default="")
    la.set_defaults(fn=cmd_lesson)
    lse = ls_s.add_parser("search")
    lse.add_argument("query")
    lse.add_argument("--limit", type=int, default=None, help="default: [lessons].max_results")
    lse.set_defaults(fn=cmd_lesson)

    rs = s.add_parser("research")
    rs.add_argument("--id", default="")
    rs.add_argument("--question", required=True)
    rs.add_argument("--claim", default="")
    rs.add_argument("--mechanism", default="")
    rs.add_argument("--falsifier", default="")
    rs.add_argument("--probe", default="")
    rs.add_argument("--probe-output", default="")
    rs.add_argument("--verdict", required=True)
    rs.add_argument("--sources", default="")
    rs.add_argument("--budget", default="")
    rs.add_argument("--item", default="")
    rs.set_defaults(fn=cmd_research)

    bg = s.add_parser("bug")
    bg_s = bg.add_subparsers(dest="bug_cmd", required=True)
    bf = bg_s.add_parser("found")
    bf.add_argument("--id", default="")
    bf.add_argument("--summary", required=True)
    bf.add_argument("--item", default="")
    bf.set_defaults(fn=cmd_bug)
    bx = bg_s.add_parser("fixed")
    bx.add_argument("id")
    bx.add_argument("--regression-test", default="")
    bx.add_argument("--lesson", default="")
    bx.add_argument("--lesson-title", default="")
    bx.add_argument("--lesson-rule", default="")
    bx.set_defaults(fn=cmd_bug)

    se = s.add_parser("session")
    se_s = se.add_subparsers(dest="session_cmd", required=True)
    ss = se_s.add_parser("start")
    ss.add_argument("--model", default="")
    ss.add_argument("--tool", default="")
    ss.set_defaults(fn=cmd_session)
    sp = se_s.add_parser("prompt")
    sp.add_argument("session")
    sp.add_argument("--text")
    sp.add_argument("--item", default="")
    sp.set_defaults(fn=cmd_session)
    sn = se_s.add_parser("note")
    sn.add_argument("session")
    sn.add_argument("--text")
    sn.add_argument("--item", default="")
    sn.set_defaults(fn=cmd_session)
    sd = se_s.add_parser("end")
    sd.add_argument("session")
    sd.add_argument("--summary", default="")
    sd.set_defaults(fn=cmd_session)

    rp = s.add_parser("replay", help="reconstruct the decision history from the log")
    rp.add_argument("--out", default="")
    rp.add_argument("--verify", action="store_true")
    rp.set_defaults(fn=cmd_replay)

    rc = s.add_parser("recover", help="find crashed agents' work (exit 2 = nothing)")
    rc.add_argument("--item", default="")
    rc.add_argument("--apply", action="store_true")
    rc.set_defaults(fn=cmd_recover)

    s.add_parser("doctor", help="integrity + health check").set_defaults(fn=cmd_doctor)
    s.add_parser("rebuild", help="re-derive the index from the log").set_defaults(fn=cmd_rebuild)

    rn = s.add_parser("render", help="regenerate the human-readable views")
    rn.add_argument("--out", default="docs/orchard")
    rn.set_defaults(fn=cmd_render)
    bd = s.add_parser("board")
    bd.add_argument("--phase", default="")
    bd.set_defaults(fn=cmd_board)
    sh = s.add_parser("show")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)

    cf = s.add_parser("config", help="print every knob, its value and its source")
    cf.add_argument("--explain", action="store_true")
    cf.add_argument("--filter", default="")
    cf.add_argument(
        "--set",
        default="",
        help="edit one key in place, e.g. --set gate.unit_tests.command 'pytest -q'",
    )
    cf.add_argument("value", nargs="?", default="", help="the value, when --set is used")
    cf.add_argument(
        "--append-toml",
        default="",
        help="append this TOML to .orchard/config.toml (validated first)",
    )
    cf.set_defaults(fn=cmd_config)

    cd = s.add_parser("cadence", help="which periodic passes are due (exit 2 = none)")
    cd.add_argument("--ran", default="")
    cd.add_argument("--note", default="")
    cd.set_defaults(fn=cmd_cadence)

    rv = s.add_parser("reviewers", help="find, list and test cross-family reviewers")
    rv_s = rv.add_subparsers(dest="reviewers_cmd", required=True)
    rvd = rv_s.add_parser("detect", help="probe well-known local endpoints")
    rvd.add_argument(
        "--write",
        action="store_true",
        help="append the discovered reviewers to .orchard/config.toml",
    )
    rvd.set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("list").set_defaults(fn=cmd_reviewers)
    rvt = rv_s.add_parser("test", help="send a tiny known-buggy diff and check the reply")
    rvt.add_argument("name", nargs="?", default="")
    rvt.set_defaults(fn=cmd_reviewers)

    rw = s.add_parser("review", help="run the configured reviewer(s) over an item's diff")
    rw.add_argument("id", nargs="?", default="")
    rw.add_argument("--gate", default="critic")
    rw.add_argument(
        "--intent",
        default="",
        help="what the change is meant to do (defaults to the item's title/body)",
    )
    rw.add_argument("--context", default="")
    rw.add_argument("--base", default="")
    rw.set_defaults(fn=cmd_review)

    ad = s.add_parser("adopt", help="install Orchard into this project for one or more agents")
    ad.add_argument(
        "--agents",
        default="",
        help="comma-separated: claude,gemini,codex,copilot,kilo (default: all)",
    )
    ad.add_argument("--docs", default="docs/orchard", help="where to write the drivers")
    ad.set_defaults(fn=cmd_adopt)

    hk = s.add_parser("hooks", help="install/inspect the enforcement git hook")
    hk_s = hk.add_subparsers(dest="hooks_cmd", required=True)
    hki = hk_s.add_parser("install")
    hki.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pre-commit hook Orchard does not manage",
    )
    hki.set_defaults(fn=cmd_hooks)
    hk_s.add_parser("uninstall").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("status").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("check-commit", help="(invoked by the hook)").set_defaults(fn=cmd_hooks)

    s.add_parser("mcp", help="run the MCP stdio server over this repository").set_defaults(
        fn=cmd_mcp
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Ctx(args)
        return int(args.fn(args, ctx))
    except KeyboardInterrupt:
        return 130
    except L.LeaseError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except (W.GitError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    raise SystemExit(main())
