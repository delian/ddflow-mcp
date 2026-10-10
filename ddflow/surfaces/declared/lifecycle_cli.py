"""How the work loop's verbs read on the command line: the human text, the stderr notes and
the `--json` bodies that are not the MCP tools' (`declared/lifecycle.py` names them).

Nothing here decides anything: the operations are `api.lifecycle` and `api.gates`, and the
executor (`surfaces/cliexec.py`) prints what these return. It imports nothing that loads the api (tests/test_declared_commands.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...core import clock
from ...core.outcome import FAIL, NOTHING, OK, REFUSED
from ...core.tier import tier_of
from ...views.markdown import new_reports_line

#: How many offending files a refusal lists before summarising the rest. Enough to see
#: whether they are build artefacts or real source -- which is the judgement the operator
#: has to make -- without burying the remedy underneath them.
MAX_LISTED_FILES = 10


def waiting(rows: list, head: str) -> str:
    """One line per registered waiter, under ``head``; "" when nobody waits."""
    if not rows:
        return ""
    lines = [head]
    for w in rows:
        what = w["item"] or (f"anything in {w['phase']}" if w["phase"] else "anything ready")
        lines.append(f"  {w['agent']} — for {what}, {clock.fmt_age(w['waiting_s'])} so far")
    return "\n" + "\n".join(lines)


# -- heartbeat, release ----------------------------------------------------------------


def heartbeat_notes(out, a, ctx) -> Iterator[str]:
    if out.data.get("globs_withheld"):
        yield f"  lease renewed WITHOUT {a['id']}'s newer globs/resources: {out.data['globs_withheld']}"


def heartbeat_text(out, a) -> str:
    waiters = out.data.get("waiters", [])
    return (
        (f"renewed {a['id']}" if out.data["renewed"] else out.reason)
        + waiting(
            waiters,
            f"{len(waiters)} agent(s) are waiting on {a['id']}. Finishing, narrowing its "
            f"globs, or releasing it wakes them:",
        )
        + (
            "\n" + new_reports_line(a["id"], out.data.get("new_reports", 0))
            if out.data.get("new_reports")
            else ""
        )
    )


def release_text(out, a) -> str:
    return f"{'released' if out.data['released'] else 'no lease on'} {a['id']}" + waiting(
        out.data.get("woke", []), "woke:"
    )


# -- abandon, remove, block, unblock ---------------------------------------------------


def abandon_text(out, a) -> str:
    return f"{a['id']} abandoned: {a['reason']}"


def remove_text(out, a) -> str:
    return f"{a['id']} removed from the queue"


def block_text(out, a) -> str:
    return f"{a['id']} blocked: {a['reason']}"


def unblock_text(out, a) -> str:
    if out.exit == NOTHING:
        return out.reason
    released = out.data["released"]
    return f"released {len(released)} item(s): {', '.join(released[:MAX_LISTED_FILES])}" + (
        " ..." if len(released) > MAX_LISTED_FILES else ""
    )


# -- gate ------------------------------------------------------------------------------


def gate_text(out, a) -> str:
    """`gate status` and `gate list`: the prose the operation built."""
    return out.data["text"]


def gate_listed(out) -> bool:
    return out.exit != FAIL


def verify_notes(out, a, ctx) -> Iterator[str]:
    if ctx.json:
        return
    if out.data.get("reason"):
        yield out.data["reason"]
    elif not out.data.get("verified"):
        yield f"\n{out.reason}"


def verify_text(out, a) -> str | None:
    if out.data.get("reason"):
        return None
    lines = []
    for r in out.data.get("results", []):
        mark = "OK  " if (r["detected"] and r["applied"]) else "FAIL"
        lines.append(f"  {mark} {r['file']}: {'detected' if r['detected'] else r['detail']}")
    if out.data.get("verified"):
        lines.append(f"\n{a['gate']} CAN fail: every registered mutation was caught.")
    return "\n".join(lines) if lines else None


def run_shown(out) -> bool:
    """A command gate that ran has an outcome to show, whatever the exit; a gate that is
    a person's or an agent's, or one that was refused, has only its instruction."""
    return out.exit != REFUSED and (bool(out.data.get("outcome")) or out.exit == OK)


def run_text(out, a) -> str:
    ev = out.data.get("evidence") or {}
    head = f"{a['gate']}: {out.data['outcome'].upper()}" + (
        f" — {ev['reason']}" if ev.get("reason") else ""
    )
    tail = ev.get("tail", "")
    return f"{tail[-1200:]}\n{head}" if tail else head


def record_notes(out, a, ctx) -> Iterator[str]:
    if out.exit == OK and out.data.get("warning"):
        yield out.data["warning"]


def record_text(out, a) -> str:
    return f"{a['id']}.{a['gate']} = {out.data['outcome']}"


# -- next ------------------------------------------------------------------------------


def next_notes(out, a, ctx) -> Iterator[str]:
    """What `next` says on stderr: an unknown `--phase` (Bde0c6e9fad), in either mode, and
    in the human one the cycles, interrupted items, auto-promotions, `pr sync` and reviews
    that are not the ready list."""
    if out.exit == FAIL:
        if out.reason:
            yield out.reason
        return
    if ctx.json:
        return
    p = out.data["_render"]["plan"]
    if p.cycles:
        yield "DEPENDENCY CYCLE(S) — nothing can be scheduled inside them:"
        for cyc in p.cycles:
            yield "  " + " -> ".join(cyc)
    # Offered, but never silently: an item RUNNING with nobody on it may have a worktree
    # full of work, and starting it from scratch loses that.
    for note in p.interrupted:
        yield f"INTERRUPTED: {note}"
    for pid in out.data["promoted"]:
        yield f"auto_promote: filed {pid}"
    for label, key in (("", "changes"), (" REFUSED", "refused"), (" UNAVAILABLE", "unavailable")):
        for line in out.data["synced"].get(key, []):
            yield f"pr sync{label}: {line}"
    if p.review:
        yield (
            f"In review ({len(p.review)}): {', '.join(i.id for i in p.review)} — "
            f"waiting on people, not on you."
        )


def next_text(out, a, ctx) -> str | None:
    if out.exit == FAIL:
        return None
    p = out.data["_render"]["plan"]
    if not p.ready:
        return "\n".join(
            [out.reason, *(f"  {b.item}: {b.reason} — {b.detail}" for b in p.blocked[:10])]
        )
    lines = [f"Ready ({p.summary()}):"]
    for it in p.ready:
        tier = tier_of(it.tags)
        lines.append(f"  {it.id}  {it.title}" + (f"  [tier:{tier}]" if tier else ""))
        if it.globs:
            lines.append(f"      writes: {', '.join(it.globs)}")
    if len(p.ready) > 1:
        lines.append("\nThese are independent — run them in parallel worktrees.")
    lines += [f"  (blocked) {b.item}: {b.reason} — {b.detail}" for b in p.blocked[:6]]
    if p.finished:
        lines.append(p.close_note())
    return "\n".join(lines)


# -- claim -----------------------------------------------------------------------------

CLAIM_BODY = (
    "item",
    "holder",
    "worktree",
    "branch",
    "base",
    "rebound",
    "port",
    "port_advice",
    "globs",
)


def claim_body(out, a) -> Any:
    # `guidance` only when something governs the item: the body is what it was otherwise.
    return out.body(CLAIM_BODY + (("guidance",) if out.data.get("guidance") else ()))


def claim_text(out, a, ctx) -> str:
    d = out.data
    msg = f"claimed {a['id']} (lease {d['ttl_s']}s, renew every {d['heartbeat_s']}s)"
    owner = ctx.tree_owner
    if owner and d["worktree"] and not d["rebound"]:
        msg += (
            f"\n  not adopted: the tree you are in is {owner}'s working tree, and you are "
            f"{ctx.log.agent_id}. Run claim from the tree without --agent (or as {owner}) "
            f"to adopt it."
        )
    if d["worktree"] and d["rebound"] and not d["here"]:
        # The item already had a tree from an earlier claim, possibly holding unmerged
        # work. It stays the item's; the caller's own tree is left alone, so the one
        # useful instruction is where to go.
        msg += (
            f"\n  worktree: {d['worktree']}  (the item's own tree, from an earlier claim)"
            f"\n  branch:   {d['branch']}"
            f"\n  It is not where you are, and yours was left alone: cd {d['worktree']} and"
            f" work there."
        )
    elif d["worktree"] and d["rebound"]:
        msg += (
            f"\n  worktree: {d['worktree']}  (the item's own tree — you are already in it)"
            f"\n  branch:   {d['branch']}\n  Carry on where you are."
        )
    elif d["worktree"] and d["adopted"]:
        # Do NOT say "cd there and work" -- the caller is already there, and telling an
        # agent to move is what the old behaviour did wrong.
        msg += (
            f"\n  worktree: {d['worktree']}  (adopted — you were already in it)"
            f"\n  branch:   {d['branch']}\n  Carry on where you are."
        )
    elif d["worktree"]:
        msg += (
            f"\n  worktree: {d['worktree']}\n  branch:   {d['branch']} (from {d['base']})"
            f"\n  cd there and work."
        )
    # Every claim says what it now covers: an agent that passed ten paths and was
    # recorded holding one had no way to tell (Bb3cb64444e).
    msg += f"\n  globs:    {', '.join(d['globs']) or '(none -- nothing is protected)'}"
    if d["port_advice"]:
        msg += f"\n  {d['port_advice']}"
    if d.get("guidance"):
        msg += "\n\n" + d["guidance"].rstrip()
    return msg


# -- complete --------------------------------------------------------------------------

COMPLETE_BODY = (
    "id",
    "sha",
    "independence",
    "forced",
    "coverage_gaps",
    "note",
    "woke",
    "bugs_closed",
)
COMPLETE_OPTIONAL = (
    "export_refresh",
    "refuted_passes",
    "progress",
    "umbrellas_completed",
    "umbrella_refused",
)


def complete_notes(out, a, ctx) -> Iterator[str]:
    for warning in out.data.get("warnings", []):
        yield f"NOTE: {warning}"
    for bid in out.data.get("bugs_closed", []):
        yield f"bug {bid} closed"
    if out.exit != OK:
        return
    if out.data.get("export_refresh"):
        yield f"  {out.data['export_refresh']['summary']}"
    for up in out.data.get("umbrellas_completed", []):
        yield f"{up} completed with its sub-tasks"
    for up, why in (out.data.get("umbrella_refused") or {}).items():
        first = (why.splitlines() or [""])[0]
        yield f"WARNING: {up} stays open -- its sub-tasks are done, but: {first}"


def complete_body(out, a) -> Any:
    return out.body(COMPLETE_BODY + tuple(k for k in COMPLETE_OPTIONAL if k in out.data))


def complete_text(out, a, ctx) -> str:
    data = out.data
    blockers = data["blockers"]
    return (
        (f"NOTE: {data['note']}\n" if data["note"] else "")
        + f"{a['id']} completed"
        + (f" as {a['sha']}" if a.get("sha") else "")
        + (f" [FORCED over {len(blockers)} unmet condition(s)]" if blockers else "")
        + waiting(data.get("woke", []), "woke:")
        + "".join(f"\nPASSED ON REFUTATION: {line}" for line in data.get("refuted_passes", []))
        + (f"\n{data['progress']}" if data.get("progress") else "")
    )


# -- merge -----------------------------------------------------------------------------

MERGE_PAYLOAD = (
    "id",
    "sha",
    "branch_head",
    "base",
    "pr",
    "branch",
    "outside_globs",
    "outside_globs_unknown",
    "merge_gate_human",
    "worktree",
    "worktree_removed",
)


def merge_extra(ctx) -> dict[str, Any]:
    """The directory this process -- and so the caller's shell -- stands in, if any."""
    try:
        return {"shell_cwd": Path.cwd()}
    except OSError:
        return {"shell_cwd": None}


def merge_shown(out) -> bool:
    return out.exit == OK


def merge_reason(out) -> str:
    """What a merge refused over uncommitted files says: the NAMES, truncated here rather
    than in the api (how many to show is a presentation decision; a caller reading JSON
    wants all of them)."""
    if out.exit != REFUSED or not out.data.get("dirty"):
        return out.reason
    dirty = out.data["dirty"]
    lines = [
        f"{len(dirty)} uncommitted file(s) in {out.data['path']} would NOT be included in "
        f"the merge:",
        *(f"  {entry}" for entry in dirty[:MAX_LISTED_FILES]),
    ]
    if len(dirty) > MAX_LISTED_FILES:
        lines.append(f"  ... and {len(dirty) - MAX_LISTED_FILES} more")
    lines.append(
        "\nCommit them, add them to .gitignore if they are build output, or pass "
        "--allow-dirty to merge without them."
    )
    return "\n".join(lines)


def merge_body(out, a) -> Any:
    local = (
        () if out.data.get("pr") else tuple(k for k in ("export_refresh", "ci") if k in out.data)
    )
    return out.body(MERGE_PAYLOAD + local)


def merge_notes(out, a, ctx) -> Iterator[str]:
    if out.exit != OK:
        return
    d = out.data
    if d.get("pr"):
        for w in d["warnings"]:
            yield f"  {w}"
        return
    if d["kept_reason"]:
        yield f"  {d['kept_reason']}"
    if d.get("merge_gate_human"):
        yield (
            f"  the merge gate is a human checkpoint: it is not recorded by merge -- ask the "
            f"operator to run `ddflow approve {a['id']} merge`"
        )
    outside = d.get("outside_globs") or []
    if d.get("outside_globs_unknown"):
        yield (
            f"  WARNING: git could not list what this landing changed, so whether it holds "
            f"paths outside {a['id']}'s globs is unknown"
        )
    if outside:
        yield (
            f"  WARNING: {len(outside)} landed path(s) outside {a['id']}'s globs -- another "
            f"item's work on the same branch?"
        )
        for p in outside[:MAX_LISTED_FILES]:
            yield f"    {p}"
    for extra in d["back_merged"]:
        yield f"  back-merged into {extra}"
    if d.get("export_refresh"):
        yield f"  {d['export_refresh']['summary']}"
    if ci := d.get("ci"):  # the base's health after the landing ([ci].on_merge)
        said = {"passed": "passed", "failed": "FAILED", "unavailable": "could not run"}.get(
            ci["status"], ci["status"]
        )
        yield f"  ci on {d['base']}: {said}" + (
            f": {', '.join(ci['failed'])}" if ci.get("failed") else ""
        )
        for bug in ci.get("bugs", []):
            task = ci.get("fix_tasks", {}).get(bug)
            yield f"    bug {bug} filed" + (f" ({task} is queued)" if task else "")


def merge_text(out, a) -> str:
    d = out.data
    if d.get("pr"):
        stacked = f", stacked on {d['stacked_on']}" if d["stacked_on"] else ""
        return (
            f"{'opened' if d['created'] else 'updated'} {d['pr']} into "
            f"{d['base']}{stacked}. {a['id']} is IN REVIEW and its lease is released — "
            f"take the next item; `ddflow pr sync` completes it once merged."
        )
    return f"merged {a['id']} ({d['sha'][:8]}) into {d['base']}"


# -- brief -----------------------------------------------------------------------------


def brief_notes(out, a, ctx) -> Iterator[str]:
    """An unknown `--phase` (Bc2acd426f4), on stderr in either mode."""
    if out.exit == FAIL and out.reason:
        yield out.reason


def brief_body(out, a) -> Any:
    return out.body() if out.exit == FAIL else out.body(("brief", "item", "ready", "approx_tokens"))


def brief_text(out, a, ctx) -> str | None:
    return None if out.exit == FAIL else out.data["text"]
