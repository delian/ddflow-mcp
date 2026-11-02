"""Release at enqueue: the agent leaves the item at the merge queue and its lane is free.

When only machine gates remain, holding a lease while the integrator waits for the suite is
hold-and-wait (r-lock-cycle-selfheal). `ddflow queue add ITEM --release` (or
`[lanes].release_mode = "auto"`) hands the item's custody to the queue: the entry is marked
released, the agent's lease ends and its inbox is told it may claim the next item. The
integrator (`queue run`, any agent, no daemon) runs the machine gates left, merges and
completes the item under the author identity recorded here. A failure PARKS the entry with a
resume brief derived from the entry and the item, and wakes the lane: nothing is lost.

This module is the decisions and the messages; the entry model is `services.mergequeue`.
Judgement gates are never run or implied here: release is refused unless each is recorded
passed on the CURRENT tree (or skipped with its reason). Release never forces a completion.
"""

from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path

from . import gates as G
from . import inbox as IB
from . import jobs as J
from . import mergequeue as MQ
from .completion import changed_paths

EVENT = "lane.released_for_gate"


def machine_gates(cfg) -> tuple[str, ...]:
    """The extra gates the integrator runs for a released entry. ci and merge are its own."""
    return tuple(g for g in cfg.lanes.release_machine_gates if g not in ("ci", "merge"))


def tree_of(wt: Path) -> str:
    """The identity of the tree ``wt`` holds now, as a gate records it."""
    return G.tree_identity(wt)


def refusal(repo: Path, cfg, st, it, wt: Path, *, unit_tests: bool, caller: str) -> str:
    """Why ``it`` may not be released at enqueue, or "". The queue's own readiness (every
    other gate carries an outcome) is asked separately by `api.mergequeue_add.refusal`."""
    mode = cfg.lanes.release_mode
    if mode == "off":
        return "[lanes].release_mode = 'off': releasing at enqueue is not allowed here"
    lease = it.lease
    if lease is None or lease.holder != caller:
        who = lease.holder if lease else "nobody"
        return f"{it.id} is held by {who}, not by {caller}: only its holder may release it"
    owed = MQ.owed_gates(repo, cfg, st, it, unit_tests=unit_tests, also=machine_gates(cfg))
    if owed:
        return "gates that must carry an outcome before a release: " + ", ".join(owed)
    return _judgement(repo, cfg, st, it, wt)


def _judgement(repo: Path, cfg, st, it, wt: Path) -> str:
    defs = G.load_gates(repo, cfg)
    s = G.status(st, cfg, it.id, defs, diff=lambda: changed_paths(repo, it))
    asked = [g for g in cfg.lanes.release_gates if g in s.pipeline and g not in s.not_applicable]
    for g in asked:
        rec = it.gates.get(g)
        out = it.gate_outcome(g)
        if out == "skipped":
            continue
        if out != "passed" or rec is None:
            return f"judgement gate {g} is {out or 'not recorded'}: it must be passed first"
        if not G.is_tree(str((rec.evidence or {}).get("tree_sha", "")), wt):
            return (
                f"the tree changed after {g} passed (or {g} recorded no tree): "
                f"re-run {g} on this tree, then release"
            )
    return ""


def tree_changed(e: MQ.Entry, wt: Path) -> str:
    """For the integrator: "" while the tree still is the one released, else why not."""
    if e.tree and not G.is_tree(e.tree, wt):
        return "the item's tree changed after it was released: park it, the judgement gates no longer vouch for it"
    return ""


def event_data(author: str, lane: str, tree: str, judgement: list[str], gates: list[str]) -> dict:
    return {"holder": lane, "author": author, "tree": tree, "judgement": judgement, "gates": gates}


def ready_text(item: str) -> str:
    return (
        f"{item} was handed to the merge queue: the integrator runs the remaining machine "
        f"gates, merges and completes it. You hold no lease on it; claim your next item "
        f"(`ddflow next`)."
    )


def resume_brief(e: MQ.Entry, it, why: str) -> str:
    """What a lane needs to pick a parked released item up, derived from the entry and the
    item (never from what anyone wrote about it)."""
    lines = [
        f"{e.item} was released to the merge queue and PARKED: {why}",
        f"branch {getattr(it, 'branch', '')} in {getattr(it, 'worktree', '')}",
    ]
    if e.failed:
        lines.append("failed: " + ", ".join(e.failed))
    if e.log:
        lines.append(f"full output: {e.log}")
    lines.append(
        f"Resume: `ddflow claim {e.item}`, fix, then `ddflow queue add {e.item} --release`."
    )
    return "\n".join(lines)


def notify(repo: Path, cfg, to: str, text: str, *, item: str, reason: str, sender: str) -> str:
    """Put ``text`` in ``to``'s inbox. "" or why it could not be (advisory: a full inbox
    must not undo a release or hide a parked item)."""
    if not to:
        return "no lane to tell"
    _m, why = IB.send(
        repo,
        to,
        text,
        reason=reason,
        sender=sender,
        item=item,
        max_chars=cfg.lease.inbox_max_chars,
        max_pending=cfg.lease.inbox_max_pending,
        ttl_s=cfg.lease.inbox_ttl_s,
    )
    return why


def after_run(repo: Path, cfg, it, e: MQ.Entry) -> None:
    """Tell the lane how a released entry ended: a parked one with its resume brief, a
    landed one that needs a hand with how to finish it, a clean one with one line. Advisory:
    a failure to tell never changes the entry."""
    if e.state == MQ.PARKED:
        text = resume_brief(e, it, e.reason)
    elif e.reason:
        text = f"{e.item} merged, but {e.reason}\nFinish it with `ddflow complete {e.item}`."
    else:
        text = f"{e.item} was merged and completed by the integrator."
    notify(repo, cfg, e.lane or e.agent, text, item=e.item, reason="message", sender="merge-queue")


# -- who runs the integrator (LC-integrator-trigger) -------------------------------------

#: The agent the automatically started integrator runs as.
INTEGRATOR_AGENT = "integrator"
#: Entries a warning names before it says "...".
NAMED = 5
RUN_CLI = "ddflow queue run"
RUN_MCP = "ddflow_queue action=run"


def integrator_command(repo: Path) -> str:
    """The command of an automatically started integrator job: one fixed string per
    project, so a job is recognised as an integrator by its command alone."""
    argv = [sys.executable, "-m", "ddflow", "--repo", str(repo), "--agent", INTEGRATOR_AGENT]
    return shlex.join([*argv, "queue", "run"])


def live_jobs(repo: Path, st) -> list:
    """The integrator jobs still running on this machine. Recognised by the END of the
    command: the log stores the interpreter's path redacted, never the arguments."""
    tail = shlex.join(shlex.split(integrator_command(repo))[3:])
    return [
        j for j in st.jobs.values() if j.command.endswith(tail) and J.status(j).state == "running"
    ]


def integrator_alive(repo: Path, st) -> bool:
    """Someone is integrating: the queue's lock holder, or a running integrator job."""
    return MQ.holder(repo) is not None or bool(live_jobs(repo, st))


def overdue(repo: Path, cfg, now: float | None = None) -> list[MQ.Entry]:
    """Queued entries that have waited at least ``[merge_queue].auto_run_after_s``."""
    now = time.time() if now is None else now
    after = cfg.merge_queue.auto_run_after_s
    return [e for e in MQ.live(repo) if e.state == MQ.QUEUED and now - e.queued_at >= after]


def warning(repo: Path, cfg, st, now: float | None = None) -> str:
    """The sentence ``brief``, ``queue status`` and ``doctor`` say when entries wait with
    nobody integrating, naming the call that fixes it; "" when nothing is wrong. A PARKED
    released entry is named too: its lane was woken and must claim it again."""
    lines = []
    waiting = overdue(repo, cfg, now)
    if waiting and not integrator_alive(repo, st):
        rel = [e.item for e in waiting if e.released]
        shown = ", ".join(rel[:NAMED]) + (", ..." if len(rel) > NAMED else "")
        lines.append(
            f"{len(waiting)} entries waiting ({len(rel)} released"
            + (f": {shown}" if rel else "")
            + f"), integrator not running: `{RUN_CLI}` (MCP: `{RUN_MCP}`)"
        )
    every = MQ.entries(repo)  # read once: a concurrent add must not split the two passes
    newest = {e.item: e.seq for e in every}
    for e in every:
        it = st.items.get(e.item)
        if (
            e.released
            and e.state == MQ.PARKED
            and newest[e.item] == e.seq
            and it
            and not it.terminal
        ):
            lines.append(
                f"{e.item} was released and is PARKED ({e.reason or 'see queue status'}): "
                f"lane {e.lane or e.agent or '?'} resumes it with `ddflow claim {e.item}` "
                f"(MCP: `ddflow_claim id={e.item}`)"
            )
    return "\n".join(lines)
