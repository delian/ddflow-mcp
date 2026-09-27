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
import json
import sys
from typing import Any

from ..api import items as A_ITEMS
from ..api import lifecycle as A_LIFECYCLE
from ..api import reporting as A_REPORTING
from ..core.model import GATE_OUTCOMES, fold
from ..infra import worktree as W
from ..services import gates as G
from ..services import leases as L
from ..services.adopt import AGENT_TARGETS
from .commands.config import (  # noqa: F401  -- moved out of this module
    _config_set,
    _workflow_problems,
    _write_config,
)
from .commands.decisions import cmd_decision
from .commands.gates import cmd_gate
from .commands.knowledge import (
    cmd_bug,
    cmd_history,
    cmd_lesson,
    cmd_memory,
    cmd_recall,
    cmd_research,
    cmd_session,
)
from .commands.lifecycle import (
    cmd_abandon,
    cmd_block,
    cmd_brief,
    cmd_claim,
    cmd_complete,
    cmd_heartbeat,
    cmd_merge,
    cmd_next,
    cmd_release,
    cmd_remove,
    cmd_unblock,
)
from .commands.operations import cmd_cadence, cmd_cleanup, cmd_import
from .commands.queue import cmd_phase_add, cmd_split, cmd_task_add
from .commands.reporting import (
    cmd_board,
    cmd_doctor,
    cmd_rebuild,
    cmd_recover,
    cmd_render,
    cmd_replay,
    cmd_show,
    cmd_status,
)
from .commands.review import cmd_review, cmd_reviewers
from .commands.setup import (
    cmd_adopt,
    cmd_companions,
    cmd_config,
    cmd_help,
    cmd_hooks,
    cmd_init,
    cmd_prompts,
    help_topics,
)
from .commands.workflow import cmd_workflow
from .context import (
    FAIL,
    NOTHING,
    OK,
    REFUSED,
    Ctx,
    _csv,
    _require_item,
)


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


def cmd_approve(a, c: Ctx) -> int:
    """A person clears, or refuses, a human-approval gate.

    CLI ONLY, on purpose, and `tests/test_mcp_parity.py` records the exemption with its
    reason. A human checkpoint an agent can satisfy through the MCP surface is not a
    human checkpoint — it is a second `gate record` with a longer name.
    """
    # `_require_item` FIRST, like every sibling. Without it `approve` was the one
    # gate-writing path with no existence check: `_h_gate` folds through `_item`, which
    # CREATES an item for an unknown subject, so a typo'd id printed "approved", exited
    # 0, and materialised a phantom task carrying a human approval -- while the item the
    # operator meant to approve stayed unapproved. That is the exact opposite of what
    # this command is for.
    if _require_item(c, a.id) is None:
        return FAIL
    try:
        line = G.approve(
            c.log,
            c.cfg,
            a.id,
            a.gate,
            gates=c.gates,
            note=a.note or "",
            reject=bool(a.reject),
            reason=a.reason or "",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    c.out(line, {"id": a.id, "gate": a.gate, "approved": not a.reject, "line": line})
    return OK


def cmd_progress(a, c: Ctx) -> int:
    """What work has actually been done, aggregated from the log."""
    from ..api import progress as _progress
    from ..core import progress as PR

    out = _progress(c.repo, a.id or "")
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        # The ROW ARRAY, unchanged. `ddflow progress --json` has always emitted a list
        # and callers index it; the Outcome carries more, and the `payload` entry on the
        # MCP tool keeps that surface identical too.
        print(json.dumps(out.data["rows"], indent=2, default=str))
        return OK

    rows_d = out.data["rows"]
    if not rows_d:
        print("No work recorded yet.")
        return NOTHING
    # The TABLE renders from the wire rows -- no second fold. `summary()` carries every
    # column: counts for attempts/commits, seconds held, the holder list.
    print(f"{'item':<14} {'state':<10} {'att':>3} {'held':>9} {'gates':>5} {'commits':>7}  holders")
    for d in rows_d:
        secs = d["held_seconds"]
        held = f"{secs / 60:.1f}m" if secs else "-"
        print(
            f"{d['item']:<14} {d['state']:<10} {d['attempts']:>3} {held:>9} "
            f"{d['gate_runs']:>5} {d['commits']:>7}  "
            f"{', '.join(sorted(set(d['holders']))) or '-'}"
        )
    if a.id:
        # The ONLY path that folds again, and only for one item. The per-attempt detail
        # (who held it, how it ended, gates passed and failed) is richer than the wire
        # summary, which flattens `attempts` to a count -- so this needs the objects and
        # the table above does not. Scoped to `--id` rather than paid on every call.
        events = c.log.read_all()
        rows = [r for r in PR.work(events, fold(events, strict=False)).values() if r.item == a.id]
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
    total = sum(d["held_seconds"] for d in rows_d)
    print(
        f"\n{len(rows_d)} item(s) · {total / 3600:.1f} agent-hours recorded · "
        f"{sum(d['commits'] for d in rows_d)} commit(s)"
    )
    return OK


def cmd_loops(a, c: Ctx) -> int:
    """Report circular references and runtime loops. Exit 2 when there are none.

    Renders the SAME `Outcome` the MCP surface returns, rather than computing a second
    view of the same answer. That is the B37 shape: one description of a result, two
    presentations derived from it — not two presentations kept in step by hand.
    """
    from ..api import loops as _loops
    from ..core import progress as PR

    out = _loops(c.repo)
    if c.json:
        print(json.dumps(out.data["findings"], indent=2))
        return out.exit
    if not out.data["findings"]:
        print(
            f"No loops detected ({out.data['events']} events, "
            f"{out.data['items']} items).\nChecked: " + ", ".join(out.data["checked"]) + "."
        )
        return out.exit
    # Reconstructed from the dicts the Outcome already carries. Calling `PR.detect`
    # again here would re-read and re-FOLD the whole log for a second copy of an answer
    # already in hand -- turning one O(events) pass into two, on the surface whose
    # entire job is to render what the layer below computed.
    for f in (PR.LoopFinding(**d) for d in out.data["findings"]):
        print(f"\n{f.render()}")
    print(
        f"\n{out.reason}. Thresholds are [loops] knobs; `ddflow config --explain --filter loops`."
    )
    return out.exit


#: How each event kind reads in a timeline. Absent kinds fall back to the kind name,
#: which is honest — a new event type shows up as itself rather than being silently
#: dropped from the history, which is the failure `replay` had with decisions.
def cmd_mcp(a, c: Ctx) -> int:
    from ..surfaces.mcp import serve

    serve(c.repo)
    return OK


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    # PLR0915 (statement count), suppressed HERE rather than for the whole file. argparse
    # construction is inherently one long sequence of near-identical statements, and
    # splitting it into a dozen `_add_x_parser` helpers would move the length without
    # reducing what actually matters: how much you must read to know what the CLI accepts.
    #
    # The file-wide ignore this replaces also covered C901 and PLR0912 for ~45 other
    # functions, three of which were over the limit and nobody knew (B163).
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
        add.add_argument("--priority", type=int, default=A_ITEMS.DEFAULT_PRIORITY)
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
    nx.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
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

    ap = s.add_parser(
        "approve",
        help="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
    )
    ap.add_argument("id")
    ap.add_argument("gate")
    ap.add_argument("--note", help="what you looked at, for the record")
    ap.add_argument("--reject", action="store_true", help="refuse it; --reason required")
    ap.add_argument("--reason", help="why it was rejected — a 'no' nobody can act on is a stall")
    ap.set_defaults(fn=cmd_approve)

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

    ub = s.add_parser(
        "unblock", help="release a blocked item back into the queue (exit 2 = not blocked)"
    )
    ub.add_argument("id")
    ub.add_argument("--note", default="", help="why it is work again")
    ub.set_defaults(fn=cmd_unblock)

    mg = s.add_parser("merge", help="merge an item's branch from the primary checkout")
    mg.add_argument("id")
    mg.add_argument("--message", default="")
    mg.add_argument("--keep", action="store_true")
    mg.add_argument("--allow-dirty", action="store_true")
    mg.set_defaults(fn=cmd_merge)

    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument(
        "--check-recovery", action="store_true", default=A_LIFECYCLE.DEFAULT_CHECK_RECOVERY
    )
    br.set_defaults(fn=cmd_brief)

    me = s.add_parser(
        "memory", help="operational facts about this machine/repo, shown at session start"
    )
    me_s = me.add_subparsers(dest="memory_cmd", required=True)
    ma = me_s.add_parser("add", help="remember one fact (refused over [memory] max_chars)")
    ma.add_argument("text")
    ma.add_argument("--tags", default="")
    ma.add_argument("--id", default="", help="re-record (correct) an existing memory")
    ma.set_defaults(fn=cmd_memory)
    ml = me_s.add_parser("list", help="live memories, newest first (exit 2 = none)")
    ml.add_argument("--query", default="", help="rank by relevance instead of recency")
    ml.add_argument("--limit", type=int, default=0)
    ml.add_argument("--all", action="store_true", help="include forgotten memories")
    ml.set_defaults(fn=cmd_memory)
    mf = me_s.add_parser("forget", help="stop believing a memory; kept, with the reason")
    mf.add_argument("id")
    mf.add_argument("--reason", required=True)
    mf.set_defaults(fn=cmd_memory)

    ls = s.add_parser("lesson")
    ls_s = ls.add_subparsers(dest="lesson_cmd", required=True)
    la = ls_s.add_parser("add")
    la.add_argument("--id", default="")
    la.add_argument("--title", required=True)
    la.add_argument("--rule", default="")
    la.add_argument("--why", default="")
    la.add_argument("--how", default="")
    la.add_argument(
        "--summary",
        default="",
        help="the lesson in one paragraph; what docs/ddflow/LESSONS-SUMMARY.md is made of",
    )
    la.add_argument("--tags", default="")
    la.add_argument("--seen-in", default="")
    la.add_argument("--supersedes", default="")
    la.add_argument(
        "--pattern",
        default="",
        help="regex this lesson forbids. Scans NOW and stores WHICH sites match, so "
        "`lesson verify` can name what reappeared — a count could only say it got worse",
    )
    la.add_argument(
        "--globs", default="", help="comma-separated globs to scan (default: all tracked files)"
    )
    la.set_defaults(fn=cmd_lesson)
    lv = ls_s.add_parser(
        "verify",
        help="re-scan every lesson's pattern and name the sites that reappeared",
    )
    lv.set_defaults(fn=cmd_lesson)
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
        help="comma-separated subset: decisions,lessons,memories,research,bugs,items,prompts",
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
    rn.add_argument("--out", default=A_REPORTING.DEFAULT_RENDER_DIR)
    rn.add_argument(
        "--show",
        default="",
        help="print ONE view to stdout instead of writing files: lessons, lessons-summary, research, board",
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
        # GENERATED from the registry, never typed. A hand-kept list here drifted the
        # moment `cursor` was added, and `test_every_supported_agent_is_named_where_a_user
        # _would_look` exists because of it: a capability nobody can find is one nobody
        # uses. Generating it means adding an agent cannot leave this behind.
        help=f"comma-separated: {','.join(AGENT_TARGETS)} (default: all)",
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
        help="one of: " + ", ".join(sorted(help_topics())) + ". Omit for the overview.",
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
    co_add.add_argument(
        "--dry-run",
        action="store_true",
        help="show the exact config entry that would be written, and write nothing",
    )
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
