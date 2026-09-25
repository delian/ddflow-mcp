"""The ddflow command line — the portable surface every agent can drive.

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

from ..config import Config
from ..core.model import ABANDONED, DONE, GATE_OUTCOMES, fold
from ..core.schedule import critical_path, plan
from ..infra import tomlcfg as TC
from ..infra import worktree as W
from ..infra.log import EventLog, resolve_agent_id
from ..infra.store import Store
from ..services import gates as G
from ..services import leases as L
from ..services import sessions as session
from ..views import markdown as render

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

#: Splitting into one piece is a rename, not a split.
_MIN_SPLIT_PARTS = 2
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
        start = Path(args.repo or os.environ.get("DDFLOW_REPO") or Path.cwd())
        try:
            self.repo = W.repo_root(start)
        except W.GitError:
            self.repo = start.resolve()
        self.cfg = Config.load(self.repo)
        # `DDFLOW_AGENT` is the short alias documented in server.json and used by MCP
        # clients and the git hook, which run in an environment where passing `--agent`
        # is not possible. It was documented before it was read -- and a dead env var
        # in a published manifest is worse than an undocumented one, because operators
        # set it and nothing happens.
        # One encoding of the precedence, shared with the typed MCP path. Two copies
        # drifted the moment the second path existed: `api._load` built its log with
        # `EventLog(repo, "")`, which reads neither the env var nor the config, so the
        # two surfaces wrote the same connection's events under different identities.
        resolved, layer = resolve_agent_id(self.repo, self.cfg, args.agent or "")
        if resolved != self.cfg.agent.id:
            self.cfg.agent.id = resolved
            # The layer that actually won, not a guess from comparing values. With
            # nothing set anywhere the derived name differs from `cfg.agent.id` (""),
            # so the old code fired and recorded `env` -- and `config --explain` then
            # blamed the environment for a variable nobody had exported.
            self.cfg.sources["agent.id"] = layer
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


def _resolved(c: Ctx, obj: Any) -> Any:
    """Plain-data view with worktree paths resolved to absolute.

    The LOG stores worktree paths relative to the repo root, which is what makes a
    committed log true on every checkout. But a CALLER needs a path it can `cd` to: an
    agent handed ".ddflow-worktrees/T1" has to know what it is relative to, and will
    resolve it against its own cwd — which is frequently not the repo root. Storage
    portable, interface usable; the conversion happens here, at the boundary.
    """
    out = _plain(obj)
    if isinstance(out, dict):
        for key in ("worktree", "path"):
            val = out.get(key)
            if isinstance(val, str) and val and not os.path.isabs(val):
                out[key] = str(W.load_path(c.repo, val))
        if isinstance(out.get("lease"), dict):
            lv = out["lease"].get("worktree")
            if isinstance(lv, str) and lv and not os.path.isabs(lv):
                out["lease"]["worktree"] = str(W.load_path(c.repo, lv))
    return out


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _csv(v: str | None) -> list[str]:
    """`config.csv_list` — one parser for the comma-separated notation, not two."""
    from ..config import csv_list

    return csv_list(v)


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


def _require_item(c: Ctx, item_id: str, st=None):
    """The item, or ``None`` after reporting why — the one place that says so.

    `no such item` was written eleven times in this module and twice more, differently,
    in `services.gates` and `services.leases`. Eleven copies of a two-line check is
    eleven chances for one to forget `removed`, which is exactly what a caller acting
    on a removed item does not expect: the item folds, so `.get()` finds it, and only
    the flag says it is gone.
    """
    st = st if st is not None else c.state()
    it = st.items.get(item_id)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        print(f"no such item {item_id!r}{gone}", file=sys.stderr)
        return None
    return it


def cmd_init(a, c: Ctx) -> int:
    d = c.repo / ".ddflow"
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
    # An in-repo worktree root (the default inside a container, where a sibling path
    # would land on the ephemeral layer) must be ignored, or every worktree shows up as
    # hundreds of untracked files and the enforcement hook trips over them.
    root_gi = c.repo / ".gitignore"
    prev_gi = root_gi.read_text("utf-8") if root_gi.exists() else ""
    if ".ddflow-worktrees" not in prev_gi:
        root_gi.write_text(
            prev_gi
            + ("" if prev_gi.endswith("\n") or not prev_gi else "\n")
            + "\n# ddflow task worktrees (git worktrees; never commit them)\n"
            ".ddflow-worktrees/\n",
            "utf-8",
        )

    ga = c.repo / ".gitattributes"
    line = ".ddflow/events/*.jsonl merge=union\n"
    prev = ga.read_text("utf-8") if ga.exists() else ""
    if "ddflow/events" not in prev:
        ga.write_text(prev + ("" if prev.endswith("\n") or not prev else "\n") + line, "utf-8")
    c.store.rebuild(c.log)
    # Setup touches TRACKED files (.gitignore, .gitattributes, AGENTS.md). Leaving them
    # uncommitted makes the primary checkout dirty, and `ddflow merge` then refuses --
    # correctly, but with a message about "modified tracked files" that gives no hint
    # the cause was ddflow's own setup two commands ago. Say so here instead.
    touched = [
        rel
        for rel in (
            ".gitignore",
            ".gitattributes",
            ".ddflow",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/ddflow",
        )
        if (c.repo / rel).exists() and W.git(c.repo, "status", "--porcelain", "--", rel).out.strip()
    ]
    commit_hint = ""
    if touched:
        commit_hint = (
            "\n\n  Setup changed these files — commit them before your first merge, or "
            "the primary\n  checkout stays dirty and `ddflow merge` will refuse:\n"
            f"    git add {' '.join(touched)} && git commit -m 'ddflow: adopt'"
        )
    c.out(
        f"Initialised ddflow in {d}\n"
        f"  config: {cfgp}\n"
        f"  Next: `ddflow phase add P1 --title 'First phase'`" + commit_hint,
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
    """Add a task. Its parent may be a phase OR another task (making it a sub-task).

    Tasks can be added at ANY time, including while their parent is being worked: a
    task that turns out to contain two things is the normal case, not an exception, and
    a queue that cannot absorb that discovery pushes the work into someone's head.
    """
    st = c.state()
    parent = a.parent or a.phase
    if parent and parent not in st.items:
        print(
            f"no such parent {parent!r}. Add the phase or task first.",
            file=sys.stderr,
        )
        return FAIL
    a.phase = parent
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
    # Giving a task its first child turns it into an umbrella, and an umbrella is not
    # the thing being worked — its children are. Holding its lease from here would put
    # a live claim on globs that overlap every child's, so a SECOND agent could not
    # take one, and recovery would point at a worktree where nothing more will happen.
    # `split` already released for exactly this reason; adding a sub-task by hand is
    # the same transition by a different route, and it did not.
    parent_item = st.items.get(parent) if parent else None
    released = ""
    if parent_item and parent_item.kind == "task" and parent_item.lease:
        L.release(c.log, parent, note=f"became an umbrella when {a.id} was added")
        released = (
            f"\n  {parent} is now an umbrella, so its lease was released: the work is "
            f"in its sub-tasks, and holding it would block them."
        )
    c.out(f"task {a.id} added to {a.phase or '(no phase)'}{released}", {"id": a.id})
    return OK


def cmd_split(a, c: Ctx) -> int:
    """Split an item into sub-tasks, in place, without losing its history.

    For the commonest discovery there is: a task turns out to be two things. The
    original stays put and becomes an umbrella — it keeps its id, its lease history and
    anything already recorded against it, and it completes when its children do. Its
    declared globs are inherited by every child that does not declare its own, so the
    conflict detector keeps working while the split is half-finished.

    The alternative — closing the task and opening two new ones — loses the thread
    between the work that was planned and the work that happened, which is exactly
    what `ddflow replay` needs to reconstruct the project.
    """
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    if it.state in (DONE, ABANDONED):
        print(
            f"{a.id} is already {it.state}; splitting finished work would reopen it. "
            f"Add new tasks instead.",
            file=sys.stderr,
        )
        return REFUSED
    specs = [x for x in (a.into or []) if x.strip()]
    if len(specs) < _MIN_SPLIT_PARTS:
        print(
            "--into must be given at least twice: splitting into one piece is not a "
            "split, it is a rename (`ddflow update <id> --title ...`).",
            file=sys.stderr,
        )
        return FAIL

    # Resolve and validate EVERY child before appending anything. The loop used to
    # validate and append in one pass, so a collision on the second `--into` exited
    # non-zero having already written the first: the parent became an umbrella nobody
    # asked for, un-claimable because it now had a child and un-completable because
    # that child was open. It also never compared the specs to each other, so
    # `--into X=one --into X=two` appended two `task.added` events for one id, `fold`
    # merged them, and the split reported two children while producing one whose title
    # was silently the second spec's.
    planned: list[tuple[str, str]] = []
    for i, spec in enumerate(specs, 1):
        sub_id, _, title = spec.partition("=")
        sub_id = sub_id.strip() or f"{a.id}.{i}"
        if sub_id in st.items:
            print(f"{sub_id} already exists; choose another id", file=sys.stderr)
            return FAIL
        if sub_id in [p_id for p_id, _ in planned]:
            print(f"{sub_id} given twice in one split; each part needs its own id", file=sys.stderr)
            return FAIL
        if sub_id == a.id:
            print(f"{sub_id} cannot be its own sub-task", file=sys.stderr)
            return FAIL
        planned.append((sub_id, title.strip() or f"{it.title} (part {i})"))

    created = []
    for i, (sub_id, title) in enumerate(planned, 1):
        c.log.append(
            "task.added",
            sub_id,
            {
                "parent": a.id,
                "title": title,
                # Inherit the parent's globs unless the child declares its own: while the
                # split is half-done the children are the only things being worked, and a
                # child with no declared globs is a child the conflict detector cannot
                # protect.
                "globs": _csv(a.globs) or list(it.globs),
                "needs": _csv(a.needs) if i == 1 else [],
                "priority": it.priority,
            },
        )
        created.append(sub_id)

    if it.lease:
        # The umbrella is no longer the thing being worked; holding its lease would
        # block its own children on a glob conflict with itself.
        L.release(c.log, a.id, note=f"split into {', '.join(created)}")
    c.log.append(
        "task.updated",
        a.id,
        {"body": (it.body + "\n\n" if it.body else "") + f"Split into: {', '.join(created)}."},
    )
    c.out(
        f"{a.id} split into {len(created)} sub-task(s): {', '.join(created)}\n"
        f"  It keeps its id and history, and now completes when they do.\n"
        f"  Give each its own --globs with `ddflow update <id> --globs ...` if they "
        f"write different files — they inherited {a.id}'s, so they cannot run in "
        f"parallel until they differ.",
        {"item": a.id, "created": created},
    )
    return OK


def cmd_item_update(a, c: Ctx) -> int:
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
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
                        "interrupted": p.interrupted,
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
    # Offered, but never silently: an item RUNNING with nobody on it may have a
    # worktree full of work, and starting it from scratch loses that.
    for note in p.interrupted:
        print(f"INTERRUPTED: {note}", file=sys.stderr)
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
    """Acquire a lease and (optionally) create the worktree. Exit 3 if refused.

    Refuses an item that is already looping when `[loops].on_detect = "block"`. That
    refusal is the only thing that actually stops an agent spinning: a warning in a
    report is read by a human later, while a refused claim is read by the agent now.
    """
    from ..core import progress as PR

    events = c.log.read_all()
    st_now = fold(events, strict=False)
    looping = [
        f for f in PR.detect(events, st_now, c.cfg) if f.item == a.id and f.severity == "block"
    ]
    if looping and not a.force:
        print(f"refusing to claim {a.id}: it is already looping.", file=sys.stderr)
        for f in looping:
            print(f"  {f.render()}", file=sys.stderr)
        print(
            "\nRe-claiming it would continue the loop. Change the task, abandon it, "
            "or --force if you have fixed the underlying cause.",
            file=sys.stderr,
        )
        return REFUSED
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
            stored = W.store_path(c.repo, wt.path)
            c.log.append(
                "worktree.created",
                a.id,
                {"path": stored, "branch": wt.branch, "base": wt.base},
            )
            L.acquire(
                c.log,
                c.cfg,
                a.id,
                worktree=stored,
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


def _gates_ahead_of(st, cfg, item_id: str, gate: str) -> list[str]:
    """Pipeline gates BEFORE ``gate`` that have no outcome yet.

    The order in `gates.task_pipeline` is not decoration: a rubber-duck review
    recorded before `implement` reviewed an empty diff, and a `merge` recorded before
    `unit_tests` merged something nobody tested. Reported by default rather than
    refused, because some interleaving is legitimate and a tool that blocks on every
    harmless reordering gets `--force`d on reflex.
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


def _gate_verify(a, c: Ctx, st, it) -> int:
    """`gate verify` — can this gate go red at all?

    Its own function because it is the only gate subcommand that WRITES to the
    working tree and restores it, and burying that in a chain of `if` arms is how a
    reader misses it.
    """
    results, reason = G.verify(st, c.cfg, c.gates, a.gate, c.repo, it)
    payload = {
        "gate": a.gate,
        "reason": reason,
        "results": [
            {
                "file": r.file,
                "applied": r.applied,
                "detected": r.detected,
                "detail": r.detail,
            }
            for r in results
        ],
        # All three clauses. `results` is empty on every pre-flight failure -- unknown
        # gate, agent gate, no registered mutations, no green baseline -- and `all([])`
        # is True, so scoring on `results` alone turns each of those into a pass.
        "verified": bool(results) and not reason and all(r.ok for r in results),
    }
    if c.json:
        print(json.dumps(payload, indent=2))
        return OK if payload["verified"] else FAIL
    if reason:
        print(reason, file=sys.stderr)
        return FAIL
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        print(f"  {mark} {r.file}: {'detected' if r.detected else r.detail}")
    if payload["verified"]:
        print(f"\n{a.gate} CAN fail: every registered mutation was caught.")
        return OK
    print(
        f"\n{a.gate} did NOT catch every mutation. A gate that cannot fail is "
        f"worse than no gate — it reports success on every change and everyone "
        f"downstream reads that as evidence.",
        file=sys.stderr,
    )
    return FAIL


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
        if gd:
            from ..services import prompts as P

            try:
                print(
                    "\n"
                    + P.render(
                        P.resolve("gate_instruction", c.repo, _prompt_overrides(c)),
                        gate=gd,
                        item=a.id,
                    )
                )
            except P.TemplateError as exc:
                print(f"\n{gd.title}: {gd.prompt}   [template error: {exc}]")
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
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL

    # BEFORE the pipeline-order check, deliberately. `verify` asks whether this GATE
    # can go red at all -- a question about the gate's own definition, not about the
    # item's progress -- and with `enforce_order = "block"` it was refused for any gate
    # whose predecessors had not run, which is every gate at the moment you most want
    # to know the answer.
    if a.gate_cmd == "verify":
        return _gate_verify(a, c, st, it)

    skipped_ahead = _gates_ahead_of(st, c.cfg, a.id, a.gate)
    if skipped_ahead and c.cfg.gates.enforce_order != "off":
        note = (
            f"{a.gate} comes after {', '.join(skipped_ahead)} in the pipeline, and "
            f"{'none of those have' if len(skipped_ahead) > 1 else 'that one has not'} "
            f"run yet."
        )
        if c.cfg.gates.enforce_order == "block":
            print(
                f"{note}\nThe order is the point: reviewing a change before it is "
                f"implemented reviews nothing. Run them in order, or set "
                f"[gates].enforce_order = 'warn'.",
                file=sys.stderr,
            )
            return REFUSED
        print(f"NOTE: {note} Recording anyway ([gates].enforce_order = 'warn').", file=sys.stderr)

    if skipped_ahead and c.cfg.gates.enforce_order != "off" and a.gate_cmd in ("record", "skip"):
        # Recorded, not just printed. Whether "warn" should become "block" or "off" is
        # a judgement about how often this fires, and for as long as it only ever
        # printed, that judgement had no evidence behind it either way.
        c.log.append(
            "gate.out_of_order",
            a.id,
            {"gate": a.gate, "ahead": skipped_ahead, "policy": c.cfg.gates.enforce_order},
        )

    if a.gate_cmd == "run":
        if not gdef.is_command_gate:
            print(
                f"gate {a.gate!r} is an AGENT gate — ddflow cannot perform it.\n\n"
                f"{gdef.prompt}\n\n"
                f"When done: ddflow gate record {a.id} {a.gate} "
                f"--outcome passed --evidence '<what you ran / what it said>'",
                file=sys.stderr,
            )
            return NOTHING
        cwd = (
            W.load_path(c.repo, it.worktree) if (gdef.cwd == "worktree" and it.worktree) else c.repo
        )
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

    The rule-set itself lives in `services.completion` — it is domain policy, and
    policy reachable only through `main(argv)` can only be tested by driving a
    subprocess. This function does what a surface should: ask, render, exit.
    """
    from ..services import completion as CM

    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL

    v = CM.verdict(st, c.cfg, a.id, repo=c.repo, model=a.model or "")
    for warning in v.warnings:
        print(f"NOTE: {warning}", file=sys.stderr)

    if not v.may_complete and not a.force:
        print(
            f"cannot complete {a.id} — {len(v.blockers)} unmet condition(s):",
            file=sys.stderr,
        )
        for b in v.blockers:
            print(f"  - {b}", file=sys.stderr)
        print(
            f"\n`ddflow gate status {a.id}` shows the pipeline. --force overrides, "
            f"and the override is recorded.",
            file=sys.stderr,
        )
        return REFUSED

    if v.coverage_note and not c.json:
        print(f"NOTE: {v.coverage_note}")

    forced = bool(v.blockers and a.force)
    c.log.append(
        "item.completed",
        a.id,
        {
            "sha": a.sha or "",
            "kind": it.kind,
            "forced": forced,
            "overridden": v.blockers if a.force else [],
        },
    )
    L.release(c.log, a.id, note="completed")
    c.out(
        f"{a.id} completed"
        + (f" as {a.sha}" if a.sha else "")
        + (f" [FORCED over {len(v.blockers)} unmet condition(s)]" if v.blockers else ""),
        {
            "id": a.id,
            "sha": a.sha or "",
            "independence": v.independence,
            "forced": forced,
            # Both surfaces, always. This used to print only in human mode, so an agent
            # over MCP -- which is always JSON -- completed the item and was never told
            # a gate had not run: the one fact most worth surfacing, invisible on
            # precisely the surface that needed it.
            "coverage_gaps": v.coverage_gaps,
            "note": v.coverage_note,
        },
    )
    return OK


def cmd_abandon(a, c: Ctx) -> int:
    """Stop work on an item without completing it, with a recorded reason.

    Distinct from `block`: a blocked item is waiting for something and will resume,
    an abandoned one will not. The phase completion check treats only `done` and
    `abandoned` as settled, so an item you decided against stops holding its phase open
    — which it otherwise does forever, since nothing else can ever finish it.
    """
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    if it.state == DONE and not a.force:
        print(
            f"{a.id} is already done; abandoning it would rewrite finished history. "
            f"--force if you really mean it.",
            file=sys.stderr,
        )
        return REFUSED
    c.log.append("item.abandoned", a.id, {"reason": a.reason, "kind": it.kind})
    if it.lease:
        L.release(c.log, a.id, note=f"abandoned: {a.reason}")
    c.out(f"{a.id} abandoned: {a.reason}", {"id": a.id, "reason": a.reason})
    return OK


def cmd_remove(a, c: Ctx) -> int:
    """Take an item out of the queue entirely.

    The event log is append-only, so this RECORDS a removal rather than deleting
    anything: the item and everything that happened to it stay in the history and in
    `ddflow replay`, which is what keeps the record honest about work that was
    planned and then dropped.
    """
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    # Any item with work beneath it, not just a phase. The `kind == "phase"` guard
    # predates sub-tasks: removing a task umbrella left its children live but
    # unreachable, because `State.tasks(phase)` walks `descendants()` and `children()`
    # skips a removed node — so the phase view reported "nothing actionable" while two
    # open tasks sat under the hole.
    kids = [t.id for t in st.open_descendants(a.id)]
    if kids and not a.force:
        print(
            f"{a.id} still has {len(kids)} task(s): {', '.join(kids[:8])}.\n"
            f"Remove them first, or --force to orphan them.",
            file=sys.stderr,
        )
        return REFUSED
    dependents = [o.id for o in st.items.values() if not o.removed and a.id in o.needs]
    if dependents and not a.force:
        print(
            f"{', '.join(dependents)} depend"
            f"{'s' if len(dependents) == 1 else ''} on {a.id}. Removing it would "
            f"leave them blocked on something that no longer exists "
            f"(unknown dependencies are treated as unmet, deliberately).\n"
            f"Update them first, or --force.",
            file=sys.stderr,
        )
        return REFUSED
    if it.lease:
        L.release(c.log, a.id, note="removed from the queue")
    c.log.append(
        "phase.removed" if it.kind == "phase" else "task.removed", a.id, {"reason": a.reason or ""}
    )
    c.out(f"{a.id} removed from the queue", {"id": a.id})
    return OK


def cmd_block(a, c: Ctx) -> int:
    """Park an item on something outside the queue — a vendor, an operator decision.

    The existence check is not ceremony. `fold`'s `_h_state` reaches items through
    `_item()`, which CREATES one when the id is unknown, so this was the only mutating
    command where a typo'd id materialised a titleless phantom task — which the
    scheduler then offered to an agent as the next thing to do.
    """
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    c.log.append("item.blocked", a.id, {"reason": a.reason})
    c.out(f"{a.id} blocked: {a.reason}", {"id": a.id})
    return OK


def cmd_merge(a, c: Ctx) -> int:
    st = c.state()
    # Through `_require_item`, like every other mutating command. This was the one that
    # was not: `.get()` finds a removed item, because removal is a FLAG on an item that
    # still folds, so `ddflow merge` landed the branch of work the operator had
    # explicitly dropped from the queue and reported "merged" -- the most consequential
    # action in the package, taken on the item least likely to be wanted.
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    if not it.worktree:
        print(f"{a.id} has no worktree to merge", file=sys.stderr)
        return NOTHING
    wt = W.Worktree(
        item=a.id,
        path=W.load_path(c.repo, it.worktree),
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
    for cand in ("AGENTS.md", "CLAUDE.md", ".ddflow/RULES.md"):
        p_ = c.repo / cand
        if p_.is_file():
            rules = f"See `{cand}` (loaded separately by your agent)."
            break
    rec = L.scan(c.log, c.cfg, c.repo) if a.check_recovery else []
    # Decisions governing THIS item's files, matched by glob rather than by search:
    # the whole point is that they reach the agent without its having to suspect they
    # exist.
    from ..core.schedule import conflicts

    decisions = []
    if item and item in st.items:
        target = st.items[item]
        decisions = [
            d
            for d in st.decisions.values()
            if d.live and d.globs and conflicts(target.globs, d.globs)
        ]
        decisions += [d for d in st.decisions.values() if d.live and not d.globs]
    text = render.brief(
        st,
        c.cfg,
        p,
        repo=c.repo,
        item=item,
        lessons=lessons,
        rules=rules,
        recovery=[r for r in rec if r.salvageable],
        decisions=decisions,
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


def cmd_recall(a, c: Ctx) -> int:
    """ "Have we been here before?" — one query across everything the project remembers.

    Searches architectural decisions, lessons, research verdicts, past bugs, similar
    tasks and the operator's own earlier prompts, and returns them ranked and labelled
    by what kind of thing each is — because the answer to "should this change what I
    do" is different for a binding decision, a transferable lesson and a prompt from
    three weeks ago.

    This exists so an operator does not have to say the same thing twice and an agent
    does not have to learn the same thing twice. Both failures are invisible in the
    moment and obvious in the log.
    """
    from ..infra.store import RECALL_SOURCES, summarise_row

    c.store.ensure(c.log)
    want = _csv(a.sources) or [t for t, _, _ in RECALL_SOURCES]
    results: dict[str, list[dict]] = {}
    for table, label, _why in RECALL_SOURCES:
        if table not in want and label.lower() not in [w.lower() for w in want]:
            continue
        try:
            hits = c.store.search(table, a.query, a.limit)
        except Exception:
            hits = []
        if hits:
            results[table] = hits

    if c.json:
        labels = {table: label for table, label, _ in RECALL_SOURCES}
        print(
            json.dumps(
                {
                    table: [
                        {
                            "id": r.get("id"),
                            "kind": labels[table],
                            "headline": summarise_row(table, r)[0],
                            "body": summarise_row(table, r)[1],
                            "raw": r,
                        }
                        for r in rows
                    ]
                    for table, rows in results.items()
                },
                indent=2,
                default=str,
            )
        )
        return OK if results else NOTHING

    if not results:
        print(
            f"Nothing recalled for {a.query!r}.\n"
            f"Searched: {', '.join(t for t, _, _ in RECALL_SOURCES)}."
        )
        return NOTHING

    budget = a.max_chars
    used = 0
    for table, label, why in RECALL_SOURCES:
        rows = results.get(table)
        if not rows:
            continue
        header = f"\n## {label}  — {why}\n"
        print(header, end="")
        used += len(header)
        for r in rows:
            head, body = summarise_row(table, r)
            block = f"  [{r.get('id', '?')}] {head}\n" + (f"      {body}\n" if body else "")
            if used + len(block) > budget:
                print(f"      … truncated at {budget} chars (--max-chars to raise)")
                return OK
            print(block, end="")
            used += len(block)
    print(
        "\nRecall is a prompt to CHECK, not a verdict. A decision above is binding "
        "unless the operator says otherwise; a lesson is advice; a past prompt is "
        "context."
    )
    return OK


def _decision_add(a, c: Ctx, st) -> int:
    """Record a decision. Refuses without a stated DECISION, not merely a discussion."""
    did = a.id or _auto_id("D", a.title, a.decision or "")
    if not a.decision:
        print(
            "--decision is required: the record must say what was DECIDED, not only "
            "what was discussed.",
            file=sys.stderr,
        )
        return FAIL
    c.log.append(
        "decision.recorded",
        did,
        {
            "title": a.title,
            "context": a.context or "",
            "decision": a.decision,
            "consequences": a.consequences or "",
            "alternatives": a.alternatives or "",
            "globs": _csv(a.globs),
            "tags": _csv(a.tags),
            "sources": _csv(a.sources),
            "status": a.status,
            "decided_by": a.by or "",
            "item": a.item or "",
            "supersedes": _csv(a.supersedes),
        },
    )
    extra = f"; supersedes {a.supersedes}" if a.supersedes else ""
    if not _csv(a.globs):
        extra += (
            "\n  NOTE: no --globs, so this decision cannot be surfaced automatically "
            "to an agent working the code it governs. It will only be found by search."
        )
    c.out(f"decision {did} recorded ({a.status}){extra}", {"id": did})
    return OK


def _decision_supersede(a, c: Ctx, st) -> int:
    """Replace a decision. Never deletes one: how the architecture got here is the
    part a rebuild most needs."""
    if a.id not in st.decisions:
        print(f"no such decision {a.id!r}", file=sys.stderr)
        return FAIL
    if not a.by:
        print(
            "--by <new decision id> is required: a decision is never simply deleted, "
            "it is replaced by one that says what is true now.",
            file=sys.stderr,
        )
        return FAIL
    c.log.append("decision.superseded", a.id, {"by": a.by, "reason": a.reason or ""})
    c.out(f"{a.id} superseded by {a.by}", {"id": a.id, "by": a.by})
    return OK


def _decision_show(a, c: Ctx, st) -> int:
    """One decision in full."""
    d = st.decisions.get(a.id)
    if not d:
        print(f"no such decision {a.id!r}", file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(_plain(d), indent=2, default=str))
        return OK
    head = f"{d.id} — {d.title}\n  status {d.status}"
    if d.superseded_by:
        head += f" (superseded by {d.superseded_by})"
    if d.decided_by:
        head += f" · decided by {d.decided_by}"
    print(head)
    for label, val in (
        ("Context", d.context),
        ("Decision", d.decision),
        ("Consequences", d.consequences),
        ("Alternatives rejected", d.alternatives),
    ):
        if val:
            print(f"\n{label}:\n  {val}")
    if d.globs:
        print(f"\nGoverns: {', '.join(d.globs)}")
    return OK


def _decision_applicable(a, c: Ctx, st) -> int:
    """Decisions governing an item's declared files.

    The mechanism that makes a decision CONSULTED rather than merely filed: an agent
    about to write a set of paths is handed the decisions about them, without having
    to know they exist or guess a search term.
    """
    from ..core.schedule import conflicts

    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    hits = [d for d in st.decisions.values() if d.live and d.globs and conflicts(it.globs, d.globs)]
    wide = [d for d in st.decisions.values() if d.live and not d.globs]
    if c.json:
        print(
            json.dumps(
                {
                    "applicable": [_plain(d) for d in hits],
                    "project_wide": [_plain(d) for d in wide],
                },
                indent=2,
                default=str,
            )
        )
        return OK if (hits or wide) else NOTHING
    if not hits and not wide:
        print(
            f"No architectural decisions govern {a.id}'s files "
            f"({', '.join(it.globs) or 'no globs declared'})."
        )
        return NOTHING
    for d in hits:
        print(f"  [{d.id}] {d.title}\n      {d.decision}")
    for d in wide:
        print(f"  [{d.id}] {d.title}  (project-wide)\n      {d.decision}")
    return OK


def _decision_search(a, c: Ctx, st) -> int:
    """Free-text search across decisions."""
    hits = c.store.search("decisions", a.query, a.limit)
    if c.json:
        print(json.dumps(hits, indent=2, default=str))
        return OK if hits else NOTHING
    if not hits:
        print("no matching decisions")
        return NOTHING
    for h in hits:
        print(f"  [{h['id']}] {h['title']}\n      {(h.get('decision') or '')[:200]}")
    return OK


def _decision_list(a, c: Ctx, st) -> int:
    """Decisions in force; --all includes the superseded ones."""
    live = [d for d in st.decisions.values() if d.live]
    dead = [d for d in st.decisions.values() if not d.live]
    rows = live + dead if getattr(a, "all", False) else live
    if c.json:
        print(json.dumps([_plain(d) for d in rows], indent=2, default=str))
        return OK if rows else NOTHING
    if not rows:
        print(
            "No architectural decisions recorded.\n"
            "  ddflow decision add --title '...' --decision '...' --globs 'src/x/*'"
        )
        return NOTHING
    for d in sorted(rows, key=lambda x: x.at):
        flag = ""
        if not d.live:
            flag = f"  [{d.status}"
            flag += f" -> {d.superseded_by}]" if d.superseded_by else "]"
        print(f"  {d.id:<14} {d.title}{flag}")
        if d.globs:
            print(f"                 governs {', '.join(d.globs)}")
    if dead and not getattr(a, "all", False):
        print(
            f"\n({len(dead)} superseded; --all to include them — the history of how "
            f"the architecture got here is kept, never deleted)"
        )
    return OK


def cmd_decision(a, c: Ctx) -> int:
    """Architectural decisions: record them, consult them, supersede them.

    A thin dispatcher. Each subcommand is its own function because they share nothing
    but the loaded state, and reading them interleaved obscured that.
    """
    st = c.store.ensure(c.log)
    return {
        "add": _decision_add,
        "supersede": _decision_supersede,
        "show": _decision_show,
        "applicable": _decision_applicable,
        "search": _decision_search,
        "list": _decision_list,
    }.get(getattr(a, "decision_cmd", "") or "list", _decision_list)(a, c, st)


def cmd_status(a, c: Ctx) -> int:
    """One answer to "what is the state of this project?".

    Written for a human asking in a chat window, which is a different question from
    any of the machine views: it wants the shape of the thing, not a table.
    """
    from ..core import progress as PR

    events = c.log.read_all()
    st = fold(events, strict=False)
    tracked = PR.work(events, st)
    loops = PR.detect(events, st, c.cfg)
    p = plan(st, c.cfg, agent=c.log.agent_id)
    rec = L.scan(c.log, c.cfg, c.repo)

    phases = st.phases()
    tasks = st.tasks()
    done = [t for t in tasks if t.state == "done"]
    running = [t for t in tasks if t.state == "running"]
    blocked = [b for b in p.blocked if b.reason == "deps"]
    hours = sum(w.total_seconds for w in tracked.values()) / 3600
    commits = sum(len(w.commits) for w in tracked.values())

    if c.json:
        print(
            json.dumps(
                {
                    "phases": {
                        "total": len(phases),
                        "done": sum(1 for x in phases if x.state == "done"),
                    },
                    "tasks": {
                        "total": len(tasks),
                        "done": len(done),
                        "running": len(running),
                        "ready": len(p.ready),
                        "blocked": len(blocked),
                    },
                    "completed_tasks": [
                        {"id": t.id, "title": t.title, "sha": t.merged_sha} for t in done
                    ],
                    "in_flight": [
                        {"id": t.id, "title": t.title, "holder": t.lease.holder if t.lease else ""}
                        for t in running
                    ],
                    "ready_now": [{"id": t.id, "title": t.title} for t in p.ready],
                    "agent_hours": round(hours, 2),
                    "commits": commits,
                    "decisions": len([d for d in st.decisions.values() if d.live]),
                    "lessons": len(st.lessons),
                    "open_bugs": len([b for b in st.bugs.values() if b.open]),
                    "loops": [f.__dict__ for f in loops],
                    "recoverable": [_plain(r) for r in rec if r.salvageable],
                },
                indent=2,
                default=str,
            )
        )
        return OK

    print(f"# {c.repo.name}\n")
    print(
        f"{len(done)}/{len(tasks)} tasks complete across {len(phases)} phase(s); "
        f"{hours:.1f} agent-hours, {commits} commit(s).\n"
    )
    if done:
        print("Completed:")
        for t in sorted(done, key=lambda x: x.completed_at)[-12:]:
            print(
                f"  [x] {t.id:<12} {t.title}" + (f"  ({t.merged_sha[:8]})" if t.merged_sha else "")
            )
    if running:
        print("\nIn flight:")
        for t in running:
            print(f"  [~] {t.id:<12} {t.title}" + (f"  — {t.lease.holder}" if t.lease else ""))
    if p.ready:
        print("\nReady to start:")
        for t in p.ready[:8]:
            print(f"  [ ] {t.id:<12} {t.title}")
    if blocked:
        print(f"\nBlocked on dependencies: {', '.join(b.item for b in blocked[:8])}")
    extras = []
    if st.decisions:
        extras.append(
            f"{len([d for d in st.decisions.values() if d.live])} architectural decision(s)"
        )
    if st.lessons:
        extras.append(f"{len(st.lessons)} lesson(s)")
    open_bugs = [b for b in st.bugs.values() if b.open]
    if open_bugs:
        extras.append(f"{len(open_bugs)} OPEN bug(s)")
    if extras:
        print("\nRecorded: " + " · ".join(extras))
    if rec:
        salv = [r for r in rec if r.salvageable]
        print(
            f"\n⚠ {len(rec)} recoverable situation(s)"
            + (f", {len(salv)} may contain unsaved work" if salv else "")
            + " — `ddflow recover`"
        )
    if loops:
        print(f"\n⚠ {len(loops)} loop finding(s) — `ddflow loops`")
    if not loops and not rec:
        print("\nNothing looping, nothing to recover.")
    return OK


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
        print(json.dumps([_resolved(c, r) for r in found], indent=2, default=str))
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


def cmd_progress(a, c: Ctx) -> int:
    """What work has actually been done, aggregated from the log."""
    from ..core import progress as PR

    events = c.log.read_all()
    st = fold(events, strict=False)
    tracked = PR.work(events, st)
    rows = [r for r in tracked.values() if not a.id or r.item == a.id]
    if a.id and not rows:
        print(f"no such item {a.id!r}", file=sys.stderr)
        return FAIL
    rows.sort(key=lambda r: (-r.total_seconds, r.item))

    if c.json:
        print(json.dumps([r.summary() for r in rows], indent=2, default=str))
        return OK
    if not rows:
        print("No work recorded yet.")
        return NOTHING
    print(f"{'item':<14} {'state':<10} {'att':>3} {'held':>9} {'gates':>5} {'commits':>7}  holders")
    for r in rows:
        held = f"{r.total_seconds / 60:.1f}m" if r.total_seconds else "-"
        print(
            f"{r.item:<14} {r.state:<10} {len(r.attempts):>3} {held:>9} "
            f"{r.gate_runs:>5} {len(r.commits):>7}  "
            f"{', '.join(sorted(set(r.holders))) or '-'}"
        )
    if a.id and rows:
        r = rows[0]
        print(f"\n{r.item} — {r.title}")
        for i, att in enumerate(r.attempts, 1):
            print(
                f"  attempt {i}: {att.holder} · {att.seconds / 60:.1f}m · "
                f"ended {att.ended_by or 'still open'} · "
                f"{att.gates_passed} passed / {att.gates_failed} failed"
            )
        for gate, outcomes in sorted(r.gate_outcomes.items()):
            print(f"  gate {gate:<14} {' -> '.join(outcomes)}")
    total = sum(r.total_seconds for r in rows)
    print(
        f"\n{len(rows)} item(s) · {total / 3600:.1f} agent-hours recorded · "
        f"{sum(len(r.commits) for r in rows)} commit(s)"
    )
    return OK


def cmd_loops(a, c: Ctx) -> int:
    """Report circular references and runtime loops. Exit 2 when there are none."""
    from ..core import progress as PR

    events = c.log.read_all()
    st = fold(events, strict=False)
    findings = PR.detect(events, st, c.cfg)
    if c.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
        return FAIL if findings else NOTHING
    if not findings:
        print(
            f"No loops detected ({len(events)} events, {len(st.items)} items).\n"
            f"Checked: dependency cycles, repeat claims, gate flapping, reopened "
            f"items, duplicate work, stalled queue."
        )
        return NOTHING
    for f in findings:
        print(f"\n{f.render()}")
    blocking = [f for f in findings if f.severity == "block"]
    print(
        f"\n{len(findings)} finding(s)"
        + (f", {len(blocking)} blocking" if blocking else "")
        + ". Thresholds are [loops] knobs; `ddflow config --explain --filter loops`."
    )
    return FAIL


def cmd_cleanup(a, c: Ctx) -> int:
    """Classify every ddflow worktree and branch; with --apply, land the safe ones."""
    from ..services import cleanup as CL

    st = c.store.ensure(c.log)
    plan = CL.survey(c.repo, c.cfg, st)
    if c.json and not a.apply:
        print(
            json.dumps(
                {
                    "trees": [_plain(t) for t in plan.trees],
                    "stale_branches": [_plain(t) for t in plan.stale_branches],
                },
                indent=2,
                default=str,
            )
        )
        return OK if (plan.trees or plan.stale_branches) else NOTHING
    if not plan.trees and not plan.stale_branches:
        print("Nothing to clean up: no ddflow worktrees or branches remain.")
        return NOTHING
    for t in plan.trees + plan.stale_branches:
        print("  " + t.render())
    if plan.needs_human:
        print(
            f"\n{len(plan.needs_human)} tree(s) hold UNCOMMITTED work and are never "
            f"touched automatically. Inspect each before deciding."
        )
    if not a.apply:
        actionable = plan.actionable
        print(
            f"\n{len(actionable)} safe action(s) available. Re-run with --apply to "
            f"perform them; dirty trees are excluded whatever you pass."
        )
        return OK
    done = CL.apply(c.repo, c.cfg, plan)
    for line in done:
        print(f"  {line}")
    c.out(f"\n{len(done)} action(s) performed.", {"performed": done})
    return OK


def cmd_doctor(a, c: Ctx) -> int:
    problems: list[str] = []
    notes: list[str] = []
    problems += c.log.verify()
    if not (c.repo / ".ddflow").exists():
        problems.append("no .ddflow directory — run `ddflow init`")
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
    # The workflow's own coherence. A pipeline naming a gate that has no definition is
    # the one config error that is both silent and permanent -- every item entering the
    # pipeline blocks on an outcome that can never be recorded -- so it belongs in the
    # command an operator runs when something is wrong, not only in `ddflow workflow`.
    from ..services import workflow as WF

    for f in WF.check(c.cfg, c.gates):
        # `f.subject: f.detail`, not `f.render()` -- doctor prefixes its own severity,
        # and "PROBLEM: [problem] ..." reads like a bug in the tool reporting the bug.
        (problems if f.level == WF.PROBLEM else notes).append(f"{f.subject}: {f.detail}")

    from ..core import progress as PR
    from ..infra import container as CT

    # The reviewer endpoints are fetched HERE and handed down: `infra.container` must
    # not reach up into `services.review` to get them.
    try:
        from ..services.review import load_reviewers

        urls = [(r.name, r.base_url) for r in load_reviewers(c.repo) if r.enabled]
    except Exception:
        urls = []
    notes.extend(CT.warnings(c.repo, c.cfg, urls))
    for f in PR.detect(c.log.read_all(), st, c.cfg):
        (problems if f.severity == "block" else notes).append(f.render())
    rec = L.scan(c.log, c.cfg, c.repo)
    for r in rec:
        (problems if r.salvageable else notes).append(f"{r.kind}: {r.item} — {r.advice}")
    ghosts = [
        w.get("worktree", "")
        for w in W.list_worktrees(c.repo)
        if c.cfg.worktree.branch_prefix.rstrip("/") in w.get("branch", "")
    ]
    known = {str(W.load_path(c.repo, it.worktree)) for it in st.items.values() if it.worktree}
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


#: `render --show <name>` targets, and the function that produces each.
#:
#: `--show` exists so the MCP `resources/read` handler can serve these through the CLI
#: like everything else. It used to fold the log itself — a second data path in a
#: module whose whole premise is "one implementation, two doors", re-wiring EventLog
#: and fold without the config and agent resolution `Ctx` does.
_RENDERABLE = {
    "lessons": render.lessons_md,
    "research": render.research_md,
    "board": render.board,
}


def cmd_render(a, c: Ctx) -> int:
    show = getattr(a, "show", "")
    if show:
        fn = _RENDERABLE.get(show)
        if fn is None:
            print(
                f"unknown view {show!r}; known: {', '.join(sorted(_RENDERABLE))}",
                file=sys.stderr,
            )
            return FAIL
        st = c.state()
        # `board` takes the config; the two markdown views do not. Inspected rather
        # than try/except'd, because a TypeError raised INSIDE a renderer would
        # otherwise be caught and retried with the wrong arity.
        import inspect

        params = inspect.signature(fn).parameters
        print(fn(st, c.cfg) if len(params) > 1 else fn(st))
        return OK
    st = c.store.ensure(c.log)
    files = render.write_views(c.repo, st, c.cfg, subdir=a.out)
    c.out("\n".join(str(f) for f in files), {"files": [str(f) for f in files]})
    return OK


def cmd_board(a, c: Ctx) -> int:
    print(render.board(c.state(), c.cfg, phase=a.phase or ""))
    return OK


def cmd_show(a, c: Ctx) -> int:
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    if c.json:
        print(json.dumps(_resolved(c, it), indent=2, default=str))
        return OK
    print(f"{it.id} [{it.kind}] {it.title}\n  state {it.state}")
    if it.needs:
        print(f"  needs {', '.join(it.needs)}")
    if it.globs:
        print(f"  globs {', '.join(it.globs)}")
    if it.lease:
        print(f"  lease {it.lease.holder} ({it.lease.remaining_s(time.time()):.0f}s left)")
    if it.worktree:
        print(f"  worktree {W.load_path(c.repo, it.worktree)} [{it.branch}]")
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
        path = c.repo / ".ddflow" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = prev.rstrip() + "\n\n" + a.append_toml.strip() + "\n"
        # The MERGED text, semantically. This used to validate `Config.load(c.repo)` --
        # the config already on DISK -- and then only the SYNTAX of the merge, so an
        # unknown section or knob was written and every later command failed to load
        # the file. A writer that validates the state it is replacing has checked
        # nothing.
        try:
            Config.check(tomllib.loads(merged))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            print(f"appending this would break the config: {exc}", file=sys.stderr)
            return FAIL
        TC.atomic_write(path, merged)
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


def _toml_literal(value: str) -> str:
    """A TOML literal for `value`, quoting it unless it already is one."""
    if re.fullmatch(r"(true|false|-?\d+(\.\d+)?|\[.*\]|\{.*\})", value.strip()):
        return value
    return json.dumps(value)  # quote + escape as a TOML basic string


def _is_header(line: str, header: str) -> bool:
    """Is this line the `[section]` header, allowing a trailing comment?

    `[gates]  # how work is checked` did not match an exact compare, so the upsert
    appended a SECOND `[gates]` -- and TOML forbids declaring a table twice, which means
    every later edit failed with a parse error pointing at a line the operator did not
    write. Commenting your own config should not disable the tool that edits it.
    """
    return line.split("#", 1)[0].strip() == header


#: TOML's two multi-line string delimiters. Built rather than written so this module's
#: own source does not have to escape them.
_TRIPLES = ('"' * 3, "'" * 3)


def _toml_lines(text: str) -> list[tuple[str, bool]]:
    """Every line paired with "is this line INSIDE a multi-line string?".

    The one thing a TOML line editor has to know. A hand-written agent prompt is a
    triple-quoted value, and a line inside one reading `command = <what you ran>`
    matched the key scan -- so `--command` was written INTO the prompt, the real key
    was never set, and `ddflow workflow` reported the result coherent. Exit 0: a
    silently dropped knob, and every future task handed a tampered instruction. A `[`
    in the same prose ended the section scan and inserted the new key mid-sentence.

    Counts delimiters rather than parsing. The alternative is a TOML round-trip, and
    the one thing worse than a line editor here is a writer that silently discards the
    comments this file carries most of its reasoning in.
    """
    out: list[tuple[str, bool]] = []
    delim = ""
    for raw in text.splitlines():
        inside = bool(delim)
        rest = raw
        while rest:
            if delim:
                hit = rest.find(delim)
                if hit < 0:
                    break
                rest = rest[hit + 3 :]
                delim = ""
                continue
            starts = [(rest.find(d), d) for d in _TRIPLES if d in rest]
            if not starts:
                break
            at, d = min(starts)
            if "#" in rest[:at]:
                break  # the opener is inside a comment
            delim = d
            rest = rest[at + 3 :]
        out.append((raw, inside))
    return out


def _value_span(lines: list[tuple[str, bool]], start: int) -> int:
    """The index one past the end of the value beginning at `lines[start]`.

    A value spans lines two ways: a bracketed array written one entry per line, which
    is how a person writes a ten-gate pipeline, and a multi-line string. Replacing only
    the first line left the rest orphaned -- `task_pipeline = ["x"]` followed by a
    stray `]` -- which TOML rejects, so every later edit was refused with a parse error
    blaming the operator for the editor's mistake.
    """
    depth = 0
    for i in range(start, len(lines)):
        raw, inside = lines[i]
        body = "" if inside else raw.split("#", 1)[0]
        depth += body.count("[") + body.count("{") - body.count("]") - body.count("}")
        open_string = i + 1 < len(lines) and lines[i + 1][1]
        if depth <= 0 and not open_string:
            return i + 1
    return len(lines)


def _toml_upsert(text: str, dotted: str, literal: str) -> str:
    """Set one `<section>.<key>` in TOML text, in place, preserving comments.

    Pure, and takes TEXT rather than a path, so several edits compose into one write.
    Applying them one file-write at a time would leave the config half-updated when the
    third of four is rejected -- and a half-applied workflow change is the state nobody
    can reason about.
    """
    section, _, key = dotted.rpartition(".")
    lines = _toml_lines(text)
    header = f"[{section}]"
    try:
        start = next(
            i for i, (ln, inside) in enumerate(lines) if not inside and _is_header(ln, header)
        )
    except StopIteration:
        body = "\n".join(ln for ln, _ in lines).rstrip()
        return (f"{body}\n\n" if body else "") + f"{header}\n{key} = {literal}\n"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if not lines[i][1] and lines[i][0].lstrip().startswith("[")
        ),
        len(lines),
    )
    i = start + 1
    while i < end:
        raw, inside = lines[i]
        stripped = raw.lstrip()
        if inside or stripped.startswith(("#", ";")) or "=" not in stripped:
            i += 1
            continue
        if stripped.split("=")[0].strip() == key:
            lines[i : _value_span(lines, i)] = [(f"{key} = {literal}", False)]
            return "\n".join(ln for ln, _ in lines).rstrip() + "\n"
        i = max(i + 1, _value_span(lines, i))
    lines.insert(end, (f"{key} = {literal}", False))
    return "\n".join(ln for ln, _ in lines).rstrip() + "\n"


def _write_config(
    repo: Path, pairs: list[tuple[str, str]], *, dry_run: bool = False, check_workflow: bool = True
) -> tuple[str, str]:
    """Apply every `(dotted, value)` edit, validate ONCE, write ONCE, under a lock.

    Returns `(error, new_text)`; a non-empty error means nothing was written. The order
    is the whole point -- compose, check the RESULT, then replace the file atomically --
    because a writer that validates the state it is replacing has checked nothing, and
    a truncating write interrupted halfway leaves an empty config that loads as "no
    overrides at all" without saying so.

    Two validations, not one. `Config.check` is the SCHEMA: is every section and knob
    real. `workflow.check` is the MEANING: does the result hang together. Without the
    second, `workflow gate X --required` (with no pipeline) exited 0 having created the
    exact inert requirement that `ddflow workflow` then reports as a problem -- the
    writer manufacturing a defect its own reader diagnoses.

    Only NEW problems are refused. Refusing on any problem at all would mean a config
    already broken could never be repaired by the tool that reports it broken.
    """
    import tomllib

    from ..services import workflow as WF

    path = repo / ".ddflow" / "config.toml"
    with TC.locked(path):
        text = path.read_text("utf-8") if path.exists() else ""
        before = _workflow_problems(repo, text) if check_workflow else set()
        for dotted, value in pairs:
            if "." not in dotted:
                return f"{dotted!r} is not <section>.<key>, e.g. gate.unit_tests.command", text
            text = _toml_upsert(text, dotted, _toml_literal(value))
        try:
            Config.check(tomllib.loads(text))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return f"that edit would break the config: {exc}", text
        if check_workflow:
            introduced = sorted(_workflow_problems(repo, text) - before)
            if introduced:
                return (
                    "that edit would leave the workflow incoherent:\n  "
                    + "\n  ".join(introduced)
                    + f"\nNothing was written. ({WF.PROBLEM} findings are refused at "
                    f"the point of writing; `ddflow workflow` reports any that are "
                    f"already there.)",
                    text,
                )
        if not dry_run:
            TC.atomic_write(path, text)
    return "", text


def _workflow_problems(repo: Path, text: str) -> set[str]:
    """The workflow problems a given config TEXT would have. Never raises.

    Loaded from the text rather than from disk, so the result can be judged before it
    becomes the state. A text that will not even load has no workflow problems to
    report -- `Config.check` is what catches that, and reporting it twice in different
    words is how an operator learns to read neither message.
    """
    import tomllib

    from ..services import workflow as WF
    from ..services.gates import GateDef, load_gates

    try:
        data = tomllib.loads(text)
        cfg = Config()
        cfg._apply(data, "file")
        gates = load_gates(repo, cfg)
        # `load_gates` overlays `[gate.*]` from the FILE, and this text is not on disk
        # yet -- so a gate being defined in the very same call was invisible, and
        # defining `lint` and piping it in one command refused itself for naming an
        # undefined gate. Judge the candidate, not the predecessor.
        for gid, spec in (data.get("gate") or {}).items():
            g = gates.get(gid) or GateDef(id=gid)
            for field, value in (spec or {}).items():
                if hasattr(g, field):
                    setattr(g, field, value)
            gates[gid] = g
        return {f"{f.subject}: {f.detail}" for f in WF.check(cfg, gates) if f.level == WF.PROBLEM}
    except Exception:
        return set()


def _config_set(a, c: Ctx) -> int:
    """`ddflow config --set <section>.<key> <value>` — edit one key in place.

    Exists because appending is not always possible: TOML forbids a duplicate table, so
    once a section is present the documented "append a block" path fails. Editing in
    place also preserves the surrounding comments, which for this file carry most of
    the reasoning.
    """
    err, _text = _write_config(c.repo, [(a.set, a.value)])
    if err:
        print(err, file=sys.stderr)
        return FAIL
    c.out(
        f"{a.set} = {_toml_literal(a.value)}",
        {"key": a.set, "value": a.value, "path": str(c.repo / ".ddflow" / "config.toml")},
    )
    return OK


def _workflow_view(c: Ctx):
    from ..services import workflow as WF
    from ..services.review import load_reviewers

    try:
        reviewers = load_reviewers(c.repo)
    except Exception:
        reviewers = []
    return WF.describe(c.repo, c.cfg, c.gates, reviewers=reviewers, state=c.state())


def _render_workflow(v) -> str:
    """The rules in force, as prose a human reads once and an agent can act on."""

    out = ["# The workflow this project runs", ""]
    out.append("Every task passes through these gates, in order. An item cannot be")
    out.append("completed until each carries an outcome.")
    out.append("")
    for g in v.gates:
        if not g.in_task:
            continue
        marks = []
        if g.required:
            marks.append("required")
        if g.evidence:
            marks.append("evidence required")
        if g.reviewer == "different_family":
            marks.append("needs a different-family reviewer")
        if g.kind == "command":
            marks.append("proven able to fail" if g.provable else "NOT proven able to fail")
        if g.kind == "undefined":
            marks.append("UNDEFINED")
        tail = f"  ({', '.join(marks)})" if marks else ""
        out.append(f"  {g.position:>2}. {g.id:<14}{g.kind:<10}{tail}")
        if g.command:
            out.append(f"      $ {g.command}")
        elif g.prompt:
            first = g.prompt.strip().splitlines()[0] if g.prompt.strip() else ""
            out.append(f"      asks: {first[:96]}")
    out.append("")
    out.append(f"A phase passes through: {', '.join(v.phase_pipeline)}")
    out.append("")
    out.append("## The rules, and where each came from")
    out.append("")
    for key, (value, source) in v.rules.items():
        line = f"  {key:<38} {value!s:<28} [{source}]"
        if key == "gates.enforce_order":
            rate = v.order_violation_rate
            line += (
                "  — no gate recorded yet"
                if rate is None
                else f"  — fired on {v.order_violations} of {v.order_recordings} "
                f"recording(s), {rate:.0%}"
            )
        out.append(line)
    out.append("")
    if v.reviewers:
        out.append("## Reviewers")
        out.append("")
        for r in v.reviewers:
            out.append(f"  {r['name']:<20} {r['model']:<32} family={r['family'] or '?'}")
        out.append("")
    else:
        out.append("## Reviewers: none configured — the `critic` gate cannot run.")
        out.append("   `ddflow reviewers detect --write` finds one.")
        out.append("")
    if v.overridden_prompts:
        out.append(f"## Rewritten locally: {', '.join(v.overridden_prompts)}")
        out.append("")
    out.append(f"Commit hook: {'installed' if v.hook_installed else 'not installed'}")
    out.append("")
    if v.findings:
        out.append("## What does not hang together")
        out.append("")
        for f in v.findings:
            out.append(f"  {f.render()}")
        out.append("")
    else:
        out.append("Nothing incoherent: every gate in a pipeline is defined, every")
        out.append("required gate is in one, and each has a command or a prompt.")
        out.append("")
    out.append("Change any of it with `ddflow workflow pipeline|gate|drop`, or by")
    out.append("editing .ddflow/config.toml. Both are validated before anything is")
    out.append("written. " + ("" if not v.findings else "Fix the problems above first."))
    return "\n".join(out)


def _workflow_show(a, c: Ctx) -> int:

    v = _workflow_view(c)
    payload = {
        "task_pipeline": v.task_pipeline,
        "phase_pipeline": v.phase_pipeline,
        "gates": [_plain(g) for g in v.gates],
        "rules": {k: {"value": val, "source": src} for k, (val, src) in v.rules.items()},
        "reviewers": v.reviewers,
        "overridden_prompts": v.overridden_prompts,
        "hook_installed": v.hook_installed,
        "order_violations": v.order_violations,
        "order_recordings": v.order_recordings,
        "order_violation_rate": v.order_violation_rate,
        "findings": [_plain(f) for f in v.findings],
        "coherent": not v.problems,
    }
    if c.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_render_workflow(v))
    return FAIL if v.problems else OK


def _workflow_pipeline(a, c: Ctx) -> int:
    which = a.which
    ids = [x.strip() for x in a.gates.split(",") if x.strip()]
    if not ids:
        print(
            "a pipeline with no gates is a project with no checks at all; name at least one",
            file=sys.stderr,
        )
        return FAIL
    unknown = [g for g in ids if g not in c.gates]
    if unknown:
        # BEFORE writing. An unknown id in a pipeline is permanent, silent damage:
        # every item entering it blocks forever and `gate record` refuses the id.
        near = {u: [g for g in sorted(c.gates) if g.startswith(u[:3])] for u in unknown}
        hint = "; ".join(
            f"{u!r}" + (f" (did you mean {near[u][0]!r}?)" if near[u] else "") for u in unknown
        )
        print(
            f"no gate is defined for {hint}. Every item entering this pipeline would "
            f"block on it forever. Define it first with `ddflow workflow gate <id> "
            f"--command ... | --prompt ...`, or leave it out.\nKnown: "
            f"{', '.join(sorted(c.gates))}",
            file=sys.stderr,
        )
        return FAIL
    key = f"gates.{which}_pipeline"
    err, _text = _write_config(c.repo, [(key, json.dumps(ids))], dry_run=a.dry_run)
    if err:
        print(err, file=sys.stderr)
        return FAIL
    verb = "would set" if a.dry_run else "set"
    c.out(
        f"{verb} {key} = {', '.join(ids)}",
        {"key": key, "gates": ids, "applied": not a.dry_run},
    )
    return OK


def _workflow_gate(a, c: Ctx) -> int:
    pairs: list[tuple[str, str]] = []
    for flag, field in (
        (a.command, "command"),
        (a.prompt, "prompt"),
        (a.cwd, "cwd"),
        (a.reviewer, "reviewer"),
        (a.title, "title"),
    ):
        if flag:
            pairs.append((f"gate.{a.id}.{field}", flag))
    if a.timeout:
        pairs.append((f"gate.{a.id}.timeout_s", str(a.timeout)))
    if a.applies_to:
        pairs.append((f"gate.{a.id}.applies_to", a.applies_to))
    if not pairs and not a.into:
        print(
            "nothing to change. Give it a --command (it runs something) or a --prompt "
            "(an agent performs it and records evidence), or --into a pipeline.",
            file=sys.stderr,
        )
        return FAIL

    existing = c.gates.get(a.id)
    defines = a.command or a.prompt or (existing and (existing.command or existing.prompt))
    if a.into and not defines:
        print(
            f"{a.id!r} has neither a command nor a prompt, so putting it in a pipeline "
            f"would block every item that reaches it. Give it one in the same call.",
            file=sys.stderr,
        )
        return FAIL

    if a.into:
        for which in ("task", "phase") if a.into == "both" else (a.into,):
            current = list(getattr(c.cfg.gates, f"{which}_pipeline"))
            if a.id in current:
                continue
            at = len(current)
            if a.after:
                if a.after not in current:
                    print(
                        f"--after {a.after!r} is not in the {which} pipeline: {', '.join(current)}",
                        file=sys.stderr,
                    )
                    return FAIL
                at = current.index(a.after) + 1
            current.insert(at, a.id)
            pairs.append((f"gates.{which}_pipeline", json.dumps(current)))
    if a.required:
        req = sorted({*c.cfg.gates.required, a.id})
        pairs.append(("gates.required", json.dumps(req)))

    err, _text = _write_config(c.repo, pairs, dry_run=a.dry_run)
    if err:
        print(err, file=sys.stderr)
        return FAIL
    verb = "would configure" if a.dry_run else "configured"
    c.out(
        f"{verb} gate {a.id}: " + ", ".join(k for k, _v in pairs),
        {"gate": a.id, "changed": [k for k, _v in pairs], "applied": not a.dry_run},
    )
    return OK


def _workflow_drop(a, c: Ctx) -> int:
    pairs: list[tuple[str, str]] = []
    removed = []
    for which in ("task", "phase"):
        current = list(getattr(c.cfg.gates, f"{which}_pipeline"))
        if a.id in current:
            current.remove(a.id)
            pairs.append((f"gates.{which}_pipeline", json.dumps(current)))
            removed.append(which)
    if a.id in c.cfg.gates.required:
        # Otherwise it becomes an inert requirement: enforced by intersecting with the
        # pipeline, so a required gate in no pipeline quietly requires nothing.
        pairs.append(("gates.required", json.dumps([g for g in c.cfg.gates.required if g != a.id])))
        removed.append("required")
    if not pairs:
        print(f"{a.id!r} is in neither pipeline; nothing to drop", file=sys.stderr)
        return NOTHING
    err, _text = _write_config(c.repo, pairs, dry_run=a.dry_run)
    if err:
        print(err, file=sys.stderr)
        return FAIL
    verb = "would drop" if a.dry_run else "dropped"
    c.out(
        f"{verb} {a.id} from: {', '.join(removed)}. Its [gate.{a.id}] definition is "
        f"left in place — put it back with `ddflow workflow gate {a.id} --into task`.",
        {"gate": a.id, "removed_from": removed, "applied": not a.dry_run},
    )
    return OK


def cmd_workflow(a, c: Ctx) -> int:
    """`ddflow workflow` — the rules in force here, and how to change them."""
    return {
        "pipeline": _workflow_pipeline,
        "gate": _workflow_gate,
        "drop": _workflow_drop,
    }.get(a.workflow_cmd or "", _workflow_show)(a, c)


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
    print("\nRecord one with: ddflow cadence --ran <name>")
    return OK


def _diff_for(c: Ctx, item_id: str, base: str = "") -> tuple[str, str]:
    """(diff, how) for an item: its worktree branch vs base, plus the working tree.

    Goes through `worktree.capture_diff`, which includes UNTRACKED files via
    intent-to-add. A plain `git diff` omits them, so the regression test an agent just
    wrote is invisible to the reviewer — which then reports, correctly given its input
    and wrongly given the facts, that the change ships no tests.
    """
    st = c.state()
    it = st.items.get(item_id)
    base = base or c.cfg.worktree.base_ref or W.default_branch(c.repo)
    wt_path = W.load_path(c.repo, it.worktree) if it and it.worktree else None
    if wt_path and wt_path.exists():
        wt = wt_path
        diff = W.capture_diff(wt, base)
        if diff.strip():
            ok, missing = W.diff_covers_everything(wt, diff)
            how = f"{base}..HEAD + working tree in {wt}"
            if not ok:
                how += f" (WARNING: {len(missing)} changed path(s) absent from the diff)"
            return diff, how
    return W.capture_diff(c.repo), f"working tree in {c.repo}"


def cmd_reviewers(a, c: Ctx) -> int:
    from ..services import review as R

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
            cfg_path = c.repo / ".ddflow" / "config.toml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            prev = cfg_path.read_text("utf-8") if cfg_path.exists() else ""
            cfg_path.write_text(prev.rstrip() + "\n" + "".join(blocks), "utf-8")
            print(f"\nappended {len(blocks)} reviewer block(s) to {cfg_path}")
        else:
            print("\nAdd to .ddflow/config.toml (or re-run with --write):")
            print("".join(blocks))
        return OK

    if a.reviewers_cmd == "presets":
        for name, spec in sorted(R.PRESETS.items()):
            where = spec.get("base_url") or spec.get("command", "")
            print(f"  {name:<12} {spec['kind']:<9} {where}")
        print("\nAdd one with:  ddflow reviewers add --preset <name> [--model M]")
        return OK

    if a.reviewers_cmd == "add":
        preset = dict(R.PRESETS.get(a.preset, {}))
        if a.preset and not preset:
            print(f"unknown preset {a.preset!r}; `ddflow reviewers presets`", file=sys.stderr)
            return FAIL
        if a.model:
            preset["model"] = a.model
        if a.base_url:
            preset["base_url"] = a.base_url
        if a.gates:
            preset["gates"] = _csv(a.gates)
        name = a.name or a.preset or preset.get("model", "reviewer")
        preset.setdefault("family", R.family_of(preset.get("model", "")))
        preset.pop("launch", None) if a.no_launch else None
        body = [f'\n[[reviewer]]\nname = "{name}"']
        launch = preset.pop("launch", None)
        for k, v in preset.items():
            body.append(f"{k} = {json.dumps(v)}")
        if launch:
            body.append("launch = " + json.dumps(launch))
        block = "\n".join(body) + "\n"
        path = c.repo / ".ddflow" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        path.write_text(prev.rstrip() + "\n" + block, "utf-8")
        note = ""
        if preset.get("api_key_env"):
            note = (
                f"\n  Set ${preset['api_key_env']} in your environment. The KEY is "
                f"never written to the config — only the variable's name, because "
                f"this file is committed."
            )
        c.out(f"added reviewer {name!r} to {path}{note}", {"name": name})
        return OK

    revs = R.load_reviewers(c.repo)
    if a.reviewers_cmd == "list":
        if not revs:
            print("No reviewers configured. Run `ddflow reviewers detect --write`.")
            return NOTHING
        unclassified = []
        for r in revs:
            fam = r.resolved_family()
            if not fam and r.enabled:
                unclassified.append(r.name)
            print(
                f"  {r.name:<22} {fam or '?':<12} gates={','.join(r.gates)} "
                f"{'' if r.enabled else '(disabled) '}{r.base_url} [{r.model}]"
            )
        if unclassified:
            # Not cosmetic: an unclassified reviewer cannot satisfy the
            # different-family requirement, so `complete` will refuse and the reason
            # will look like it is about the review rather than about this line.
            print(
                f"\n  {', '.join(unclassified)} have no known family (shown as '?'), so "
                f"they cannot\n  satisfy [agent].reviewer_family_must_differ. Set "
                f'`family = "..."` on each\n  in .ddflow/config.toml, or add the '
                f"model name to [agent].families."
            )
        return OK

    if a.reviewers_cmd == "test":
        targets = [r for r in revs if not a.name or r.name == a.name]
        if not targets:
            print(f"no reviewer named {a.name!r}; `ddflow reviewers list`", file=sys.stderr)
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
    from ..services import review as R

    revs = R.reviewers_for(R.load_reviewers(c.repo), a.gate)
    if not revs:
        print(
            f"No reviewer is configured for gate {a.gate!r}. "
            f"`ddflow reviewers detect --write` finds local models.\n"
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
        res = R.review(
            r,
            diff,
            intent,
            context=a.context or "",
            repo=c.repo,
            prompt_overrides=_prompt_overrides(c),
        )
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


def _prompt_overrides(c: Ctx) -> dict[str, str]:
    from dataclasses import fields as _fields

    return {
        f.name: getattr(c.cfg.prompts, f.name)
        for f in _fields(c.cfg.prompts)
        if getattr(c.cfg.prompts, f.name)
    }


def cmd_prompts(a, c: Ctx) -> int:
    from ..services import prompts as P

    ov = _prompt_overrides(c)
    if a.prompts_cmd == "list":
        rows = P.list_all(c.repo, ov)
        if c.json:
            print(
                json.dumps(
                    [
                        {
                            "name": t.name,
                            "kind": t.kind,
                            "source": t.source,
                            "path": str(t.path),
                            "chars": len(t.text),
                        }
                        for t in rows
                    ],
                    indent=2,
                )
            )
            return OK
        # Grouped, because the two kinds are used for entirely different things: a
        # template is machinery (the review prompt, the gate instruction, the MCP
        # handshake) and a command is a workflow an operator invokes. A flat list of
        # eleven names invites `prompts show mcp_instructions` expecting a workflow.
        for title, kind in (("Templates", "template"), ("Workflow commands", "command")):
            members = [t for t in rows if t.kind == kind]
            if not members:
                continue
            print(f"{title}:")
            for t in members:
                print(f"  {t.name:<24} [{t.source:<7}] {t.path}")
            print()
        print(
            "Edit any of them with `ddflow prompts eject <name>`, which copies the "
            "shipped default into .ddflow/prompts/ where it takes precedence.\n"
            "Read one with `ddflow prompts show <name>`."
        )
        return OK
    if a.prompts_cmd == "show":
        try:
            # `resolve_any`, not `resolve`: a caller should not have to know which of
            # the two registries a name lives in before it can be read. Knowing was
            # what leaked out as "unknown template 'research-companions'" -- a message
            # that listed the five templates to someone who had typed a real command
            # name correctly.
            print(P.resolve_any(a.name, c.repo, ov).text)
        except P.TemplateError as exc:
            print(str(exc), file=sys.stderr)
            return FAIL
        return OK
    if a.prompts_cmd == "eject":
        # With no name, everything -- BOTH registries. Writing only the templates is
        # the silent half of the bug this branch just fixed: `eject` is the documented
        # way to customise a prompt, so covering half the library means the documented
        # way to edit a workflow command did not exist.
        names = [a.name] if a.name else [*P.TEMPLATE_NAMES, *P.COMMANDS]
        out = c.repo / ".ddflow" / "prompts"
        out.mkdir(parents=True, exist_ok=True)
        (out / "commands").mkdir(parents=True, exist_ok=True)
        written = []
        for n in names:
            try:
                t = P.resolve_any(n, None, {})  # the SHIPPED default, not the override
            except P.TemplateError as exc:
                print(str(exc), file=sys.stderr)
                return FAIL
            # A command must land in `prompts/commands/`, which is where
            # `resolve_command` looks. Writing it beside the templates would produce a
            # file the operator edits and the tool never reads -- an override that
            # silently does nothing, which is worse than refusing to eject it.
            dst = (out / "commands" / f"{n}.md") if t.kind == "command" else (out / f"{n}.md")
            if dst.exists() and not a.force:
                print(f"  skipped {dst} (exists; --force to overwrite)")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(t.text, "utf-8")
            written.append(str(dst))
        c.out(
            "\n".join(f"  wrote {w}" for w in written)
            + "\n\nThese now take precedence over the shipped defaults. Edit them "
            "freely; they are plain text and are not parsed as code.",
            {"written": written},
        )
        return OK
    return FAIL


def cmd_hooks(a, c: Ctx) -> int:
    from ..services import enforce as E

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
                "enforces it. Run `ddflow hooks install`."
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


def _scan_companions(repo: Path, *, probe: bool = True):
    """Scan, or report why not and return None. ONE guard, used by every caller.

    The registry loader writes a careful sentence naming the file, the id and the bad
    value; letting its `ValueError` escape renders that sentence as an uncaught
    exception and makes the exit code an accident rather than a contract. Guarding it
    per call site meant two of three sites were fixed and `adopt` — documented as safe
    to re-run — still exited on a traceback, after `init` had already written files.
    """
    from ..services import companions as CO

    try:
        return CO.scan(repo, probe=probe)
    except ValueError as exc:
        print(f"{exc}\n  (in .ddflow/companions.toml or .ddflow/config.toml)", file=sys.stderr)
        return None


def cmd_companions(a, c: Ctx) -> int:
    """Which companion MCP servers serve this project's gates, and what is missing.

    Exit 0 when every default companion is registered; exit 2 when something is
    installed-but-unregistered or absent — "no data" reported as itself, never
    collapsed into "no problem". Never exits 1: a missing optional server is a gap to
    close, not a failure of this command.
    """
    from ..services import companions as CO

    statuses = _scan_companions(c.repo, probe=not getattr(a, "no_probe", False))
    if statuses is None:
        return FAIL
    by_id = {st.companion.id: st for st in statuses}

    if a.companions_cmd == "add":
        wanted = _csv(a.id) or [
            st.companion.id
            for st in statuses
            if st.companion.default and st.installed and st.companion.is_mcp  # servers only
        ]
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            print(
                f"unknown companion(s): {', '.join(unknown)}; known: {', '.join(by_id)}",
                file=sys.stderr,
            )
            return FAIL
        not_servers = [w for w in wanted if not by_id[w].companion.is_mcp]
        if not_servers:
            print(
                "not an MCP server: "
                + "; ".join(
                    f"{w} is a {by_id[w].companion.kind} tool ({by_id[w].companion.install})"
                    for w in not_servers
                )
                + ". There is no MCP config entry to write. Install it and ddflow "
                "detects it; `ddflow companions` shows it either way.",
                file=sys.stderr,
            )
            return REFUSED
        if not wanted:
            print(
                "nothing to add: no default MCP companion is installed on this "
                "machine. `ddflow companions` lists them with their install commands.",
                file=sys.stderr,
            )
            return NOTHING
        # Registering a server that is not installed would write a config entry whose
        # launch fails at the worst moment -- mid-task, as an agent reaches for the
        # tool a gate told it to use. `--force` exists for the case where the operator
        # is about to install it.
        # `is False` and `is None` are different refusals. Both block -- registering a
        # launch command that fails mid-task is the thing to avoid either way -- but
        # "not installed here" sent to an operator whose probe merely TIMED OUT makes
        # them install something they already have, and the install line printed
        # helpfully below then does nothing. Say which one it is.
        absent = [w for w in wanted if by_id[w].installed is False and not a.force]
        unknown = [w for w in wanted if by_id[w].installed is None and not a.force]
        if absent or unknown:
            if absent:
                print(
                    f"not installed here: {', '.join(absent)}. Registering one would "
                    f"write a launch command that fails mid-task. Install it first "
                    f"({'; '.join(by_id[w].companion.install for w in absent)}), or "
                    f"--force if you are about to.",
                    file=sys.stderr,
                )
            if unknown:
                print(
                    f"could not tell whether these are installed: "
                    f"{', '.join(unknown)} — "
                    + "; ".join(by_id[w].detail for w in unknown)
                    + ". That is not the same as absent. Re-run, check by hand, or "
                    "--force if you know it is there.",
                    file=sys.stderr,
                )
            return REFUSED
        agents = _csv(a.agents) or ["claude"]
        actions = [CO.register(c.repo, by_id[w].companion, ag) for w in wanted for ag in agents]
        c.out("\n".join(f"  {x}" for x in actions), {"actions": actions})
        return OK

    pipeline = list(c.cfg.gates.task_pipeline)
    cover = CO.gate_coverage(c.repo, statuses, pipeline)
    payload = {
        "companions": [
            {
                "id": st.companion.id,
                "title": st.companion.title,
                "gates": st.companion.gates,
                "state": st.state,
                "registered_in": st.registered_in,
                "detail": st.detail,
                "install": st.companion.install,
                "url": st.companion.url,
                "default": st.companion.default,
                "kind": st.companion.kind,
            }
            for st in statuses
        ],
        "gate_coverage": cover,
        "uncovered_gates": [g for g, ids in cover.items() if not ids],
    }
    # "Registered" is not a state a `cli` companion can reach -- there is nothing to
    # register. Judging one by it would make `companions` exit 2 forever the moment a
    # cli entry joined the registry, which trains the reader to ignore the exit code.
    # For those, INSTALLED is the goal state.
    gaps = [st for st in statuses if st.is_gap]
    if c.json:
        print(json.dumps(payload, indent=2))
        return NOTHING if gaps else OK

    lines = ["Companion tools", ""]
    for st in statuses:
        mark = {"registered": "[x]", "installed": "[+]", "missing": "[ ]", "unknown": "[?]"}[
            st.state
        ]
        tag = "" if st.companion.default else "  (opt-in)"
        if not st.companion.is_mcp and st.installed is True:
            mark = "[x]"
        lines.append(f"  {mark} {st.companion.id:<10s} {st.companion.title}{tag}")
        lines.append(f"       gates: {', '.join(st.companion.gates) or '—'}")
        if st.state == "registered":
            lines.append(f"       registered for: {', '.join(st.registered_in)}")
        elif st.state == "installed" and not st.companion.is_mcp:
            lines.append(
                f"       installed ({st.detail}). A {st.companion.kind} tool — the agent "
                f"shells out to it, so there is nothing to register."
            )
        elif st.state == "installed":
            lines.append(f"       installed ({st.detail}) but no agent is configured to launch it.")
            lines.append(f"       -> ddflow companions add --id {st.companion.id}")
        elif st.state == "unknown":
            lines.append(f"       not checked ({st.detail}) — re-run without --no-probe")
        else:
            lines.append(f"       not here: {st.detail}")
            lines.append(f"       -> ask the operator, then: {st.companion.install}")
            if st.companion.note:
                lines.append(f"          note: {st.companion.note}")
            if st.companion.is_mcp:
                lines.append(f"          then: ddflow companions add --id {st.companion.id}")
            if st.companion.url:
                lines.append(f"          {st.companion.url}")
        if st.companion.why:
            lines.append(f"       {st.companion.why.strip().splitlines()[0]}")
        lines.append("")
    uncovered = payload["uncovered_gates"]
    if uncovered:
        lines += [
            "Gates in this project's task pipeline with no companion behind them:",
            f"  {', '.join(uncovered)}",
            "  Not a failure — several of these are judgement an agent does directly.",
            "  It is the list to check when a gate has been passing suspiciously easily.",
            "",
        ]
    if gaps:
        # Addressed to the agent, because the agent is who reads this. ddflow does not
        # install anything itself -- running an install command on someone's machine is
        # the operator's decision -- but "here is a gap" without "here is what to do
        # about it" is a report nobody acts on.
        lines += [
            "WHAT TO DO ABOUT THIS (agent):",
            "  Tell the operator which of these are missing, what each one buys, and",
            "  what installing it would run. If they agree, run the install command",
            "  yourself and then `ddflow companions add --id <id>`. If they decline,",
            "  record the gates it serves as `unavailable` when you reach them — never",
            "  as passed on your own word.",
            "",
        ]
    print("\n".join(lines))
    return NOTHING if gaps else OK


#: How each event kind reads in a timeline. Absent kinds fall back to the kind name,
#: which is honest — a new event type shows up as itself rather than being silently
#: dropped from the history, which is the failure `replay` had with decisions.
_HISTORY_VERBS: dict[str, str] = {
    "phase.added": "phase added",
    "task.added": "task added",
    "task.updated": "updated",
    "task.removed": "removed from the queue",
    "phase.removed": "removed from the queue",
    "item.blocked": "blocked",
    "item.abandoned": "abandoned",
    "item.completed": "completed",
    "lease.acquired": "claimed",
    "lease.renewed": "heartbeat",
    "lease.released": "released",
    "lease.expired": "lease EXPIRED",
    "gate.recorded": "gate",
    "worktree.created": "worktree created",
    "worktree.removed": "worktree removed",
    "merge.performed": "merged",
    "session.started": "session opened",
    "session.prompt": "operator said",
    "session.note": "noted",
    "session.ended": "session closed",
    "lesson.recorded": "lesson",
    "decision.recorded": "DECISION",
    "decision.superseded": "decision superseded",
    "research.recorded": "research",
    "bug.found": "BUG found",
    "bug.fixed": "bug fixed",
    "cadence.ran": "cadence ran",
}


def _history_line(ev) -> str:
    """One event, as a line someone can read."""
    verb = _HISTORY_VERBS.get(ev.kind, ev.kind)
    d = ev.data or {}
    detail = (
        d.get("title")
        or d.get("text")
        or d.get("summary")
        or d.get("question")
        or d.get("reason")
        or d.get("note")
        or ""
    )
    if ev.kind == "gate.recorded":
        detail = f"{d.get('gate', '?')} = {d.get('outcome', '?')}"
    elif ev.kind == "lease.acquired":
        detail = f"by {d.get('holder', '?')}"
    elif ev.kind == "item.completed" and d.get("sha"):
        detail = f"as {d['sha'][:8]}"
    detail = " ".join(str(detail).split())[:88]
    return f"  {ev.ts[:16].replace('T', ' ')}  {ev.subject:<22.22s} {verb:<22s} {detail}"


def cmd_history(a, c: Ctx) -> int:
    """One reverse-chronological timeline of everything that happened.

    `status`, `progress`, `replay` and `recall` each answer part of "what has happened
    here", and an operator asking that question had to know which to run. This is the
    plain answer: the log, newest first, filterable.

    Ordered by `(lamport, agent, id)` like everything else — NOT by wall-clock
    timestamp. Two agents on two machines have two clocks, and sorting a merged history
    by `ts` would interleave them wrongly while looking perfectly plausible.
    """
    events = c.log.read_all()
    if a.item:
        events = [e for e in events if e.subject == a.item]
    if a.kind:
        wanted = set(_csv(a.kind))
        events = [e for e in events if e.kind in wanted or e.kind.split(".")[0] in wanted]
    if a.since:
        events = [e for e in events if e.ts >= a.since]
    events = sorted(events, key=lambda e: (e.lamport, e.agent, e.id), reverse=True)
    shown = events[: a.limit]

    if c.json:
        print(
            json.dumps(
                {
                    "total": len(events),
                    "shown": len(shown),
                    "events": [
                        {
                            "id": e.id,
                            "at": e.ts,
                            "lamport": e.lamport,
                            "agent": e.agent,
                            "kind": e.kind,
                            "subject": e.subject,
                            "data": e.data,
                        }
                        for e in shown
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return OK if shown else NOTHING
    if not shown:
        print("Nothing in the history matches.")
        return NOTHING
    print(f"{len(events)} event(s); newest {len(shown)} first:\n")
    for e in shown:
        print(_history_line(e))
    if len(events) > len(shown):
        print(f"\n  ... {len(events) - len(shown)} older. --limit to see more.")
    print(
        "\n  Ordered by Lamport clock, not wall time: two agents have two clocks, and "
        "\n  sorting a merged history by timestamp interleaves them wrongly."
    )
    return OK


def _help_topics() -> list[str]:
    """The topic names, read from the one place that defines them.

    Imported lazily and inside a function so `build_parser` does not drag the MCP tool
    table in through `help.grouped_tools`: the parser is built on EVERY invocation,
    including `ddflow next` in a hot loop.
    """
    from ..services.help import TOPICS

    return list(TOPICS)


def cmd_help(a, c: Ctx) -> int:
    """`ddflow help [topic]` — what this is, what it can do, what the workflow is.

    Not argparse's `--help`, which lists 43 subcommands alphabetically and explains
    neither what any of them is for nor which to reach for first. The pages are
    templates, so `ddflow prompts`-style overriding applies: a project can rewrite its
    own onboarding without a code change.
    """
    from ..services import help as H
    from ..services.prompts import TemplateError

    # The tool registry is handed IN: `services/` sits below `surfaces/`, so the help
    # renderer may not reach up for it. Function-local for the same reason `cmd_mcp`'s
    # is -- the parser is rebuilt on every invocation and must not drag it along.
    from .mcp import TOOLS

    try:
        text = H.render_topic(a.topic, c.repo) if a.topic else H.render_index(c.repo, tools=TOOLS)
    except TemplateError as e:
        print(str(e), file=sys.stderr)
        return FAIL
    c.out(text, {"topic": a.topic or "index", "text": text, "topics": H.TOPICS})
    return OK


def _import_verify(c: Ctx, st) -> int:
    """`ddflow import --verify` — status, still-true, and did-anyone-finish-it.

    Three exit codes because there are three answers and collapsing them loses the one
    that matters: `2` is "nothing was ever imported", which is not a failure and not a
    pass; `1` is "imported, and here is what a human still has to decide"; `0` is
    "imported and consistent".
    """
    from ..services import importer as IM

    r = IM.verify_import(c.repo, st)
    findings = r.findings
    payload = {
        "imported": r.imported,
        "total": r.total,
        "first_at": r.first_at,
        "last_at": r.last_at,
        "findings": findings,
        "tasks_without_globs": r.no_globs,
        "shipped_with_open_tasks": r.shipped_drift,
        "vanished_sources": [{"item": i, "source": src} for i, src in r.vanished],
        "empty_sources": r.empty_sources,
        "unstructured_provenance": r.unstructured,
        "branches_without_globs": r.no_globs_branches,
        # Separate from `verified` on purpose. "Nothing was imported" and "the import
        # is in good order" are different answers, and a single boolean collapses them
        # into the vacuous one: `verified: true` with `imported: {}` reads as checked-
        # and-fine to anything that does not also read the exit code -- and over MCP
        # exit 2 is not an error, so `_meta` is the only place the truth was.
        "imported_anything": bool(r.total or r.unstructured),
        "new_since_import": [
            {"kind": f.kind, "id": f.ident, "title": f.title, "source": f.source} for f in r.drift
        ],
        "notes": r.notes,
        "verified": bool(r.total) and not findings,
    }
    if c.json:
        print(json.dumps(payload, indent=2, default=str))
        return NOTHING if not r.total and not r.unstructured else (FAIL if findings else OK)

    if not r.total and not r.unstructured:
        print("Nothing in this queue was imported.")
        for n in r.notes:
            print(f"  {n}")
        return NOTHING

    when = f" between {r.first_at[:10]} and {r.last_at[:10]}" if r.first_at else ""
    lines = [f"Imported{when}:", ""]
    lines += [f"  {n:>6} {kind}(s)" for kind, n in sorted(r.imported.items())]
    if r.unstructured:
        lines.append(f"  {len(r.unstructured):>6} with prose-only provenance (older import)")
    lines.append("")
    if findings:
        lines.append("Left to decide or fix:")
        lines += [f"  - {f}" for f in findings]
    else:
        lines.append("Consistent: the queue matches the sources, and every imported")
        lines.append("task declares what it writes.")
    lines.append("")
    lines += [f"  {n}" for n in r.notes]
    lines.append("")
    lines.append(
        "`ddflow import` (no flags) shows what a re-run would add; the "
        "/import-existing-project prompt walks through the half that needs an operator."
    )
    print("\n".join(lines))
    return FAIL if findings else OK


def cmd_import(a, c: Ctx) -> int:
    """Propose what an existing project already has, so the queue starts where it is.

    Reads and reports by default; `--apply` writes. A project adopting ddflow on day
    400 has four hundred days of work, and a queue that starts empty tells an agent
    "nothing is in flight" about a repository with three branches in flight.

    Exit 2 when there is nothing to propose — "no data" reported as itself.
    """
    from ..services import importer as IM

    st = c.state()
    if a.verify:
        # `--include-done` and `--max-tasks` shape an IMPORT. Reading past them here
        # would be the silent-knob-drop shape, standing next to a flag that is loudly
        # refused two lines down.
        shaping = [
            f for f, on in (("--include-done", a.include_done), ("--max-tasks", a.max_tasks)) if on
        ]
        if shaping:
            print(
                f"--verify reports on the import that happened; {', '.join(shaping)} "
                f"shape(s) one that has not. Run them separately.",
                file=sys.stderr,
            )
            return FAIL
        if a.apply:
            # Refused rather than resolved. `--verify` READS and `--apply` WRITES, and
            # picking one silently is how an operator who asked to import ends up
            # having only looked -- or worse, the other way round.
            print(
                "--verify and --apply ask for different things: one reports on the "
                "import that happened, the other performs one. Run them separately.",
                file=sys.stderr,
            )
            return FAIL
        return _import_verify(c, st)
    # The flag overrides the knob; 0 means 'no flag given', so an operator who set
    # [importer] max_tasks in the config is not silently overruled by an argparse
    # default that looks like a choice and is not one.
    plan = IM.plan_import(
        c.repo,
        st,
        include_done=a.include_done,
        max_tasks=a.max_tasks or c.cfg.importer.max_tasks,
    )
    payload = {
        "summary": plan.summary(),
        "found": [
            {
                "kind": f.kind,
                "id": f.ident,
                "title": f.title,
                "source": f.source,
                "done": f.done,
                "needs": f.needs,
                "globs": f.globs,
            }
            for f in plan.found
        ],
        "skipped_existing": plan.skipped_existing,
        "empty_sources": plan.empty_sources,
        "notes": plan.notes,
        "applied": False,
    }

    if a.apply and plan.found:
        counts = IM.apply_import(c.repo, c.log, plan)
        payload["applied"] = True
        payload["written"] = counts
        c.out(
            "Imported: "
            + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            + "\n  Every item records where it came from. Review with `ddflow board`, "
            "then give each task its globs — an item with no declared globs is one the "
            "conflict detector cannot protect.",
            payload,
        )
        return OK

    if c.json:
        print(json.dumps(payload, indent=2, default=str))
        return OK if plan.found else NOTHING

    if not plan.found:
        print("Nothing to import.")
        for n in plan.notes:
            print(f"  {n}")
        return NOTHING

    lines = ["What this project already has (nothing written yet):", ""]
    preview = c.cfg.importer.preview_rows
    for kind in IM.KINDS:
        rows = plan.by_kind(kind)
        if not rows:
            continue
        lines.append(f"  {len(rows)} {kind}(s):")
        for f in rows[:preview]:
            mark = "[x]" if f.done else "[ ]"
            lines.append(f"    {mark} {f.ident:<28s} {f.title[:52]:<52s} {f.source}")
        if len(rows) > preview:
            lines.append(f"    ... and {len(rows) - preview} more")
        lines.append("")
    if plan.skipped_existing:
        lines.append(
            f"  {len(plan.skipped_existing)} already in the queue, left alone "
            f"(this command is safe to re-run)."
        )
        lines.append("")
    for n in plan.notes:
        lines.append(f"  NOTE: {n}")
    lines += [
        "",
        "  `ddflow import --apply` writes these. Before you do:",
        "    - the headings became phases and the checkboxes tasks, which is a GUESS;",
        "    - no task has globs unless the file declared them, and a task with no",
        "      globs is one two agents can collide on;",
        "    - dependencies are only what the file said.",
        "  The `/import-existing-project` workflow walks an agent through fixing those",
        "  WITH the operator, which is the half this command cannot do.",
    ]
    print("\n".join(lines))
    return OK


def cmd_adopt(a, c: Ctx) -> int:
    from ..services.adopt import AGENT_TARGETS, adopt

    agents = _csv(a.agents) or list(AGENT_TARGETS)
    try:
        actions = adopt(
            c.repo,
            agents,
            docs_dir=a.docs,
            install_hooks=c.cfg.enforce.install_hooks_on_setup,
            launch=a.launch,
            image=a.image,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    cmd_init(a, c)
    # Name the companion gap at adoption time. A project that adopts ddflow and stops
    # has a `standards` gate with nothing behind it and a `rules` gate reading no
    # memory -- and because an agent gate passes on an assertion, that gap is invisible
    # in exactly the way this design exists to prevent. Detection only; nothing is
    # installed, because fetching and running code on someone's machine is not a thing
    # a work-queue tool gets to do.
    # `is_gap`, so an installed command-line companion is not reported as "installed
    # here but not wired up" -- there is nothing to wire up, and the command offered
    # below would refuse it. Third of the four sites that each re-derived "goal state";
    # they all ask `Status` now.
    statuses = _scan_companions(c.repo)
    if statuses is None:
        return FAIL
    ready, absent = [], []
    for st in statuses:
        if not st.is_gap:
            continue
        (ready if st.state == "installed" else absent).append(st.companion.id)
    tail = ""
    if ready:
        tail += (
            f"\n\nInstalled here but not wired up: {', '.join(ready)}.\n"
            f"  ddflow companions add --agents {agents[0]}"
        )
    if absent:
        tail += f"\n\nNot installed: {', '.join(absent)} — `ddflow companions` has the commands."
    c.out(
        "\n".join(f"  {x}" for x in actions) + f"\n\nddflow adopted for: {', '.join(agents)}.\n"
        f"  1. set your test command in .ddflow/gates.toml\n"
        f"  2. ddflow phase add P1 --title '...'\n"
        f"  3. tell your agent: implement phase P1" + tail,
        {
            "actions": actions,
            "agents": agents,
            "companions_ready": ready,
            "companions_absent": absent,
        },
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
    from ..surfaces.mcp import serve

    serve(c.repo)
    return OK


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width)


def _starter_config() -> str:
    """The single configuration file.

    One file, not two. An earlier version also wrote `.ddflow/gates.toml` carrying a
    placeholder `unit_tests.command`, and because gates.toml wins over config.toml that
    placeholder silently overrode anything `ddflow configure` wrote -- so the documented
    way to set the test command could not set the test command. Splitting gates into
    their own file is still supported for operators who want it; it is just not the
    default, because a default that creates two sources of truth will produce two
    sources of truth.
    """
    return """# ddflow configuration — everything in one file.
# `ddflow config --explain` documents every knob. Only what you change needs to be
# here; everything else keeps its default.

# ---------------------------------------------------------------------------------
# THE ONE THING YOU MUST SET: how this project runs its tests.
# ---------------------------------------------------------------------------------
# [gate.unit_tests]
# command = "pytest -q"     # or "npm test" · "cargo test" · "go test ./..." · "make check"
#
# Set it with:   ddflow config --set gate.unit_tests.command "pytest -q"
# Until it is set, the unit_tests gate reports UNAVAILABLE — which is honest, and
# blocks completion, rather than passing vacuously.
#
# Left COMMENTED on purpose: an empty table here would collide with the block that
# `ddflow config --append-toml` writes, since TOML forbids a duplicate table, and the
# documented way to configure the project would fail on a fresh install.

# ---------------------------------------------------------------------------------
# A cross-family reviewer makes the `critic` gate real rather than self-reported.
# `ddflow reviewers detect --write` finds a local model server and fills this in.
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
# Starts at "warn" so adopting ddflow never breaks an existing repo on day one.
commit_without_lease = "warn"

[session]
brief_max_tokens = 1200
"""


# -- parser ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ddflow",
        description="A portable work-queue kernel for AI coding agents. "
        "The event log is the source of truth; everything else is derived.",
    )
    p.add_argument(
        "--repo", help="repository root (default: cwd, resolved to the primary checkout)"
    )
    p.add_argument("--agent", help="agent identity (default: host-pid). Shards the log.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    s = p.add_subparsers(dest="cmd", required=True)

    s.add_parser("init", help="create .ddflow/ in this repository").set_defaults(fn=cmd_init)

    # A phase and a task differ by two arguments. Writing both blocks out by hand is
    # how `--priority` ended up on one and not the other twice before, and how the
    # `record`/`skip` pair right below this was loop-generated for the same reason.
    def _item_parser(sub, name: str, help_text: str, fn):
        grp = s.add_parser(name, help=help_text)
        sub_p = grp.add_subparsers(dest=f"{name}_cmd", required=True)
        add = sub_p.add_parser("add")
        add.add_argument("id")
        add.add_argument("--title", default="")
        add.add_argument("--needs")
        add.add_argument("--globs")
        add.add_argument("--tags")
        add.add_argument("--body")
        add.add_argument("--priority", type=int, default=100)
        add.set_defaults(fn=fn)
        return add

    _item_parser(s, "phase", "add a phase", cmd_phase_add)
    tad = _item_parser(s, "task", "add a task", cmd_task_add)
    tad.add_argument("--phase", default="", help="owning phase")
    tad.add_argument(
        "--parent",
        default="",
        help="owning phase OR task — a task parent makes this a SUB-TASK, which "
        "carries its own globs and dependencies like any other task",
    )

    sp = s.add_parser(
        "split", help="split an item into sub-tasks in place, keeping its id and history"
    )
    sp.add_argument("id")
    sp.add_argument(
        "--into", action="append", default=[], help="repeatable: 'sub-id=title', or just 'sub-id'"
    )
    sp.add_argument(
        "--globs", default="", help="globs for the children (default: inherit the parent's)"
    )
    sp.add_argument("--needs", default="", help="dependencies for the FIRST child")
    sp.set_defaults(fn=cmd_split)

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
    gvf = g_s.add_parser(
        "verify",
        help="break what this gate guards and require it to notice (exit 1 = it cannot)",
    )
    gvf.add_argument("id")
    gvf.add_argument("gate")
    gvf.set_defaults(fn=cmd_gate)
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

    ab = s.add_parser("abandon", help="stop work on an item without completing it")
    ab.add_argument("id")
    ab.add_argument("--reason", required=True)
    ab.add_argument("--force", action="store_true")
    ab.set_defaults(fn=cmd_abandon)

    rm = s.add_parser("remove", help="take an item out of the queue (recorded, not erased)")
    rm.add_argument("id")
    rm.add_argument("--reason", default="")
    rm.add_argument("--force", action="store_true")
    rm.set_defaults(fn=cmd_remove)

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

    rc = s.add_parser(
        "recall",
        help="'have we been here before?' — search decisions, lessons, research, bugs, "
        "tasks and past prompts at once",
    )
    rc.add_argument("query")
    rc.add_argument("--limit", type=int, default=3, help="hits per source")
    rc.add_argument(
        "--sources",
        default="",
        help="comma-separated subset: decisions,lessons,research,bugs,items,prompts",
    )
    rc.add_argument("--max-chars", type=int, default=4000)
    rc.set_defaults(fn=cmd_recall)

    dc = s.add_parser("decision", help="architectural decisions: record and consult")
    dc_s = dc.add_subparsers(dest="decision_cmd", required=False)
    dca = dc_s.add_parser("add")
    dca.add_argument("--id", default="")
    dca.add_argument("--title", required=True)
    dca.add_argument("--decision", required=True, help="what was DECIDED (not what was discussed)")
    dca.add_argument("--context", default="", help="the forces: why a decision was needed")
    dca.add_argument("--consequences", default="", help="what it costs, incl. what it makes harder")
    dca.add_argument("--alternatives", default="", help="what was rejected, and why")
    dca.add_argument(
        "--globs",
        default="",
        help="the code this governs; without it the decision can only be found by search",
    )
    dca.add_argument("--tags", default="")
    dca.add_argument(
        "--sources",
        default="",
        help="where this came from: an ADR path, a URL, a commit sha (comma-separated)",
    )
    dca.add_argument("--item", default="")
    dca.add_argument("--by", default="", help="operator | agent | a name")
    dca.add_argument("--status", default="accepted", choices=["proposed", "accepted", "superseded"])
    dca.add_argument("--supersedes", default="")
    dca.set_defaults(fn=cmd_decision)
    dcl = dc_s.add_parser("list")
    dcl.add_argument("--all", action="store_true")
    dcl.set_defaults(fn=cmd_decision)
    dcs = dc_s.add_parser("show")
    dcs.add_argument("id")
    dcs.set_defaults(fn=cmd_decision)
    dcf = dc_s.add_parser("search")
    dcf.add_argument("query")
    dcf.add_argument("--limit", type=int, default=5)
    dcf.set_defaults(fn=cmd_decision)
    dcap = dc_s.add_parser("applicable", help="decisions governing an item's declared files")
    dcap.add_argument("id")
    dcap.set_defaults(fn=cmd_decision)
    dcsu = dc_s.add_parser("supersede")
    dcsu.add_argument("id")
    dcsu.add_argument("--by", required=True, help="the decision that replaces it")
    dcsu.add_argument("--reason", default="")
    dcsu.set_defaults(fn=cmd_decision)
    dc.set_defaults(fn=cmd_decision, decision_cmd="list", all=False)

    stt = s.add_parser("status", help="one answer to 'what is the state of this project?'")
    stt.set_defaults(fn=cmd_status)

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

    pg = s.add_parser("progress", help="work actually done, aggregated from the log")
    pg.add_argument("id", nargs="?", default="")
    pg.set_defaults(fn=cmd_progress)

    lp = s.add_parser("loops", help="circular references and runtime loops (exit 2 = none)")
    lp.set_defaults(fn=cmd_loops)

    cu = s.add_parser(
        "cleanup", help="classify ddflow worktrees/branches; --apply lands the safe ones"
    )
    cu.add_argument("--apply", action="store_true")
    cu.set_defaults(fn=cmd_cleanup)

    s.add_parser("doctor", help="integrity + health check").set_defaults(fn=cmd_doctor)
    s.add_parser("rebuild", help="re-derive the index from the log").set_defaults(fn=cmd_rebuild)

    rn = s.add_parser("render", help="regenerate the human-readable views")
    rn.add_argument("--out", default="docs/ddflow")
    rn.add_argument(
        "--show",
        default="",
        help="print ONE view to stdout instead of writing files: lessons, research, board",
    )
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
        help="append this TOML to .ddflow/config.toml (validated first)",
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
        help="append the discovered reviewers to .ddflow/config.toml",
    )
    rvd.set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("list").set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("presets", help="ready-made provider settings").set_defaults(fn=cmd_reviewers)
    rva = rv_s.add_parser("add", help="add a reviewer from a preset")
    rva.add_argument("--preset", default="", help="see `ddflow reviewers presets`")
    rva.add_argument("--name", default="")
    rva.add_argument("--model", default="")
    rva.add_argument("--base-url", default="")
    rva.add_argument("--gates", default="")
    rva.add_argument(
        "--no-launch",
        action="store_true",
        help="do not auto-start a local server for this reviewer",
    )
    rva.set_defaults(fn=cmd_reviewers)
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

    ad = s.add_parser("adopt", help="install ddflow into this project for one or more agents")
    ad.add_argument(
        "--agents",
        default="",
        help="comma-separated: claude,gemini,codex,copilot,kilo,cursor (default: all)",
    )
    ad.add_argument("--docs", default="docs/ddflow", help="where to write the drivers")
    ad.add_argument(
        "--launch",
        default="auto",
        choices=["auto", "uvx", "docker", "python"],
        help="how agents spawn the MCP server: 'auto' prefers uvx; 'docker' needs no "
        "Python toolchain at all",
    )
    ad.add_argument(
        "--image",
        default="ghcr.io/OWNER/ddflow:latest",
        help="container image used by --launch docker",
    )
    ad.set_defaults(fn=cmd_adopt)

    hi = s.add_parser("history", help="one timeline of everything that happened (exit 2 = nothing)")
    hi.add_argument("--item", default="", help="restrict to one item")
    hi.add_argument(
        "--kind",
        default="",
        help="comma-separated event kinds or families: 'gate', 'lease.acquired', 'decision,bug'",
    )
    hi.add_argument("--since", default="", help="ISO timestamp lower bound")
    hi.add_argument("--limit", type=int, default=40)
    hi.set_defaults(fn=cmd_history)

    wf = s.add_parser(
        "workflow",
        help="the rules this project runs by, and how to change them (exit 1 = incoherent)",
    )
    wfs = wf.add_subparsers(dest="workflow_cmd")
    wf.set_defaults(fn=cmd_workflow, dry_run=False)

    wfp = wfs.add_parser("pipeline", help="set the gates a task or phase passes through")
    wfp.add_argument("which", choices=["task", "phase"])
    wfp.add_argument("gates", help="comma-separated gate ids, in order")
    wfp.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfp.set_defaults(fn=cmd_workflow)

    wfg = wfs.add_parser("gate", help="define or change one gate")
    wfg.add_argument("id")
    wfg.add_argument("--command", default="", help="what to run; makes it a command gate")
    wfg.add_argument("--prompt", default="", help="what to ask an agent; makes it an agent gate")
    wfg.add_argument("--title", default="")
    wfg.add_argument("--cwd", default="", choices=["", "worktree", "repo"])
    wfg.add_argument(
        "--reviewer",
        default="",
        choices=["", "different_family", "same_family_ok"],
        help="require a reviewer, and whether it must be a different model family",
    )
    wfg.add_argument("--timeout", type=int, default=0, help="seconds before it is unavailable")
    wfg.add_argument("--applies-to", default="", choices=["", "task", "phase", "both"])
    wfg.add_argument(
        "--into", default="", choices=["", "task", "phase", "both"], help="add to a pipeline"
    )
    wfg.add_argument("--after", default="", help="place it after this gate (default: last)")
    wfg.add_argument("--required", action="store_true", help="an item cannot complete without it")
    wfg.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfg.set_defaults(fn=cmd_workflow)

    wfd = wfs.add_parser("drop", help="take a gate out of the pipelines (its definition stays)")
    wfd.add_argument("id")
    wfd.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfd.set_defaults(fn=cmd_workflow)

    hp = s.add_parser(
        "help",
        help="what ddflow is, what it can do, and the workflow (try: ddflow help workflow)",
    )
    hp.add_argument(
        "topic",
        nargs="?",
        default="",
        help="one of: " + ", ".join(sorted(_help_topics())) + ". Omit for the overview.",
    )
    hp.set_defaults(fn=cmd_help)

    im = s.add_parser(
        "import",
        help="propose the existing project's work, lessons and decisions (exit 2 = nothing)",
    )
    im.add_argument("--apply", action="store_true", help="write them; default is a dry run")
    im.add_argument(
        "--verify",
        action="store_true",
        help="report what was already imported and whether it is still true: source "
        "drift, vanished source files, and the globs and decisions the import left for "
        "a human (exit 1 = findings, 2 = nothing imported)",
    )
    im.add_argument(
        "--include-done",
        action="store_true",
        help="also import already-ticked items, as completed. Off by default: a "
        "finished history is not a queue.",
    )
    im.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="refuse to propose more tasks than this. 0 (the default) uses "
        "[importer] max_tasks from the config, which ships at 200. A bigger number is "
        "usually a whole history rather than a queue.",
    )
    im.set_defaults(fn=cmd_import)

    co = s.add_parser(
        "companions",
        help="companion MCP servers that serve the gates (exit 2 = a default one is missing)",
    )
    co_s = co.add_subparsers(dest="companions_cmd")
    co_list = co_s.add_parser("list", help="what is known, installed and registered")
    co_list.add_argument("--no-probe", action="store_true", help="skip the detection probes")
    co_add = co_s.add_parser("add", help="register installed companions in an agent's MCP config")
    co_add.add_argument("--id", default="", help="comma-separated; default: every installed one")
    co_add.add_argument("--agents", default="", help="comma-separated (default: claude)")
    co_add.add_argument("--force", action="store_true", help="register one that is not installed")
    co_add.add_argument("--no-probe", action="store_true", help=argparse.SUPPRESS)
    co.set_defaults(fn=cmd_companions, companions_cmd="list", no_probe=False)
    co_list.set_defaults(fn=cmd_companions)
    co_add.set_defaults(fn=cmd_companions)

    pr = s.add_parser("prompts", help="inspect and override the prompt templates")
    pr_s = pr.add_subparsers(dest="prompts_cmd", required=True)
    pr_s.add_parser("list").set_defaults(fn=cmd_prompts)
    prs = pr_s.add_parser("show")
    prs.add_argument("name")
    prs.set_defaults(fn=cmd_prompts)
    pre = pr_s.add_parser("eject", help="copy the shipped templates into .ddflow/prompts/")
    pre.add_argument("name", nargs="?", default="")
    pre.add_argument("--force", action="store_true")
    pre.set_defaults(fn=cmd_prompts)

    hk = s.add_parser("hooks", help="install/inspect the enforcement git hook")
    hk_s = hk.add_subparsers(dest="hooks_cmd", required=True)
    hki = hk_s.add_parser("install")
    hki.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pre-commit hook ddflow does not manage",
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
