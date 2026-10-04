"""`next`, `claim`, `complete`, `merge` and friends — the human surface for
`api.lifecycle`.

Nothing here decides anything. The four rules that make this path safe — a refused claim
releases its lease, adopt before creating, never remove an adopted tree, merge goes through
the existence check — are in `api/lifecycle.py`, which is what makes them testable without
an argparse Namespace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ...api import lifecycle as A
from ...core.tier import tier_of
from ...views.markdown import new_reports_line
from ..context import FAIL, MAX_LISTED_FILES, NOTHING, OK, REFUSED, Ctx


def _refused(out) -> int:
    print(out.reason, file=sys.stderr)
    return out.exit


def _next_without_plan(out, c: Ctx) -> int:
    """`next`'s (and `brief`'s) answers that render no plan: the JSON body, and a
    refusal's reason (an unknown `--phase`, Bde0c6e9fad) on stderr in either mode, with
    its exit code."""
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
    if out.exit == FAIL and out.reason:
        print(out.reason, file=sys.stderr)
    return out.exit


def _print_synced(synced: dict) -> None:
    """What the `pr sync` that `next` ran reported, each kind under its own label."""
    for label, key in (("", "changes"), (" REFUSED", "refused"), (" UNAVAILABLE", "unavailable")):
        for line in synced.get(key, []):
            print(f"pr sync{label}: {line}", file=sys.stderr)


def cmd_next(a, c: Ctx) -> int:
    """Offer the next actionable item(s). Exit 2 when nothing is actionable, 1 when
    `--phase` names no item."""
    out = A.next_(c.repo, kind=a.kind, phase=a.phase or "", agent=c.requested_agent)
    if c.json or out.exit == FAIL:
        return _next_without_plan(out, c)
    p = out.data["_render"]["plan"]
    if p.cycles:
        print("DEPENDENCY CYCLE(S) — nothing can be scheduled inside them:", file=sys.stderr)
        for cyc in p.cycles:
            print("  " + " -> ".join(cyc), file=sys.stderr)
    # Offered, but never silently: an item RUNNING with nobody on it may have a worktree
    # full of work, and starting it from scratch loses that.
    for note in p.interrupted:
        print(f"INTERRUPTED: {note}", file=sys.stderr)
    for pid in out.data["promoted"]:
        print(f"auto_promote: filed {pid}", file=sys.stderr)
    _print_synced(out.data["synced"])
    if p.review:
        print(
            f"In review ({len(p.review)}): {', '.join(i.id for i in p.review)} — "
            f"waiting on people, not on you.",
            file=sys.stderr,
        )
    if not p.ready:
        print(out.reason)
        for b in p.blocked[:10]:
            print(f"  {b.item}: {b.reason} — {b.detail}")
        return NOTHING
    print(f"Ready ({p.summary()}):")
    for it in p.ready:
        tier = tier_of(it.tags)
        print(f"  {it.id}  {it.title}" + (f"  [tier:{tier}]" if tier else ""))
        if it.globs:
            print(f"      writes: {', '.join(it.globs)}")
    if len(p.ready) > 1:
        print("\nThese are independent — run them in parallel worktrees.")
    for b in p.blocked[:6]:
        print(f"  (blocked) {b.item}: {b.reason} — {b.detail}")
    if p.finished:
        print(p.close_note())
    return OK


def cmd_claim(a, c: Ctx) -> int:
    out = A.claim(
        c.repo,
        a.id,
        globs=a.globs or "",
        note=a.note or "",
        force=a.force,
        no_worktree=a.no_worktree,
        # WHERE THE CALLER IS, not the resolved primary. Adoption depends on whether the
        # caller was already standing in a worktree, and resolving to the repo root loses
        # exactly that fact -- unless it is a tree another identity is working in, which
        # is not the caller's to adopt: then it is the primary, and a tree of its own.
        called_from=c.called_from,
        resources=a.resources or "",
        agent=c.requested_agent,
    )
    if out.exit != OK:
        return _refused(out)
    d = out.data
    msg = f"claimed {a.id} (lease {d['ttl_s']}s, renew every {d['heartbeat_s']}s)"
    owner = c.tree_owner
    if owner and d["worktree"] and not d["rebound"]:
        msg += (
            f"\n  not adopted: the tree you are in is {owner}'s working tree, and you are "
            f"{c.log.agent_id}. Run claim from the tree without --agent (or as {owner}) "
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
    c.out(
        msg,
        out.body(
            (
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
        ),
    )
    return OK


def _waiting(rows: list, head: str) -> str:
    """One line per registered waiter, under `head`; "" when nobody waits."""
    if not rows:
        return ""
    lines = [head]
    for w in rows:
        what = w["item"] or (f"anything in {w['phase']}" if w["phase"] else "anything ready")
        lines.append(f"  {w['agent']} — for {what}, {w['waiting_s'] // 60}m so far")
    return "\n" + "\n".join(lines)


def cmd_heartbeat(a, c: Ctx) -> int:
    # WHERE THE CALLER IS: the item's own tree renews its lease whoever claimed it.
    out = A.heartbeat(c.repo, a.id, agent=c.requested_agent, called_from=c.called_from)
    if out.data.get("globs_withheld"):
        print(
            f"  lease renewed WITHOUT {a.id}'s newer globs/resources: {out.data['globs_withheld']}",
            file=sys.stderr,
        )
    waiters = out.data.get("waiters", [])
    c.out(
        (f"renewed {a.id}" if out.data["renewed"] else out.reason)
        + _waiting(
            waiters,
            f"{len(waiters)} agent(s) are waiting on {a.id}. Finishing, narrowing its "
            f"globs, or releasing it wakes them:",
        )
        + (
            "\n" + new_reports_line(a.id, out.data.get("new_reports", 0))
            if out.data.get("new_reports")
            else ""
        ),
        out.body(("renewed", "waiters", "globs_withheld", "new_reports")),
    )
    return out.exit


def cmd_release(a, c: Ctx) -> int:
    out = A.release(c.repo, a.id, note=a.note or "", agent=c.requested_agent)
    c.out(
        f"{'released' if out.data['released'] else 'no lease on'} {a.id}"
        + _waiting(out.data.get("woke", []), "woke:"),
        out.body(("released", "woke")),
    )
    return out.exit


def cmd_wait(a, c: Ctx) -> int:
    """Sleep until the item (or anything) can be claimed. Progress goes to stderr, so a
    harness watching the process -- a background shell, a monitor -- sees it move."""
    out = A.wait(
        c.repo,
        item=a.item or "",
        phase=a.phase or "",
        kind=a.kind,
        globs=a.globs,
        timeout_s=a.timeout,
        poll_s=a.poll,
        agent=c.requested_agent,
        on_progress=lambda msg: print(msg, file=sys.stderr, flush=True),
    )
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    if out.exit != OK:
        print(out.reason)
        return out.exit
    d = out.data
    print(f"READY: {', '.join(d['ready'])} (after {d['waited_s']}s)")
    for f in d["freed_by"]:
        print(f"  {f}")
    print(d["advice"])
    return OK


def cmd_complete(a, c: Ctx) -> int:
    out = A.complete(
        c.repo,
        a.id,
        sha=a.sha or "",
        force=a.force,
        model=a.model or "",
        changelog=getattr(a, "changelog", "") or "",
        regression_test=getattr(a, "regression_test", None) or [],
        agent=c.requested_agent,
    )
    for warning in out.data.get("warnings", []):
        print(f"NOTE: {warning}", file=sys.stderr)
    for bid in out.data.get("bugs_closed", []):
        print(f"bug {bid} closed", file=sys.stderr)
    if out.exit != OK:
        return _refused(out)
    if out.data["note"] and not c.json:
        print(f"NOTE: {out.data['note']}")
    blockers = out.data["blockers"]
    if out.data.get("export_refresh"):
        print(f"  {out.data['export_refresh']['summary']}", file=sys.stderr)
    c.out(
        f"{a.id} completed"
        + (f" as {a.sha}" if a.sha else "")
        + (f" [FORCED over {len(blockers)} unmet condition(s)]" if blockers else "")
        + _waiting(out.data.get("woke", []), "woke:")
        + (f"\n{out.data['progress']}" if out.data.get("progress") else ""),
        out.body(
            (
                "id",
                "sha",
                "independence",
                "forced",
                "coverage_gaps",
                "note",
                "woke",
                "bugs_closed",
                *(k for k in ("export_refresh", "progress") if k in out.data),
            )
        ),
    )
    return OK


def cmd_abandon(a, c: Ctx) -> int:
    out = A.abandon(c.repo, a.id, reason=a.reason, force=a.force, agent=c.requested_agent)
    if out.exit != OK:
        return _refused(out)
    c.out(f"{a.id} abandoned: {a.reason}", out.body(("id", "reason")))
    return OK


def cmd_remove(a, c: Ctx) -> int:
    out = A.remove(c.repo, a.id, reason=a.reason or "", force=a.force, agent=c.requested_agent)
    if out.exit != OK:
        return _refused(out)
    c.out(f"{a.id} removed from the queue", out.body(("id",)))
    return OK


def cmd_block(a, c: Ctx) -> int:
    out = A.block(c.repo, a.id, reason=a.reason, reopen=a.reopen, agent=c.requested_agent)
    if out.exit != OK:
        return _refused(out)
    c.out(f"{a.id} blocked: {a.reason}", out.body(("id",)))
    return OK


def cmd_unblock(a, c: Ctx) -> int:
    out = A.unblock(c.repo, a.id, note=a.note or "", agent=c.requested_agent)
    if out.exit not in (OK, NOTHING):
        return _refused(out)
    keys = ("id", "was", "released")
    if out.exit == NOTHING:
        c.out(out.reason, out.body(keys))
        return NOTHING
    released = out.data["released"]
    c.out(
        f"released {len(released)} item(s): {', '.join(released[:MAX_LISTED_FILES])}"
        + (" ..." if len(released) > MAX_LISTED_FILES else ""),
        out.body(keys),
    )
    return OK


def cmd_merge(a, c: Ctx) -> int:
    out = A.merge(
        c.repo,
        a.id,
        message=a.message or "",
        allow_dirty=a.allow_dirty,
        allow_empty=a.allow_empty,
        keep=a.keep,
        model=a.model or "",
        branch=a.branch or "",
        called_from=c.called_from,
        shell_cwd=_shell_cwd(),
        agent=c.requested_agent,
    )
    if out.exit == REFUSED and out.data.get("dirty"):
        # The NAMES, truncated here rather than in the api: how many to show is a
        # presentation decision, and a caller reading JSON wants all of them.
        dirty = out.data["dirty"]
        print(
            f"{len(dirty)} uncommitted file(s) in {out.data['path']} would NOT be "
            f"included in the merge:",
            file=sys.stderr,
        )
        for entry in dirty[:MAX_LISTED_FILES]:
            print(f"  {entry}", file=sys.stderr)
        if len(dirty) > MAX_LISTED_FILES:
            print(f"  ... and {len(dirty) - MAX_LISTED_FILES} more", file=sys.stderr)
        print(
            "\nCommit them, add them to .gitignore if they are build output, or pass "
            "--allow-dirty to merge without them.",
            file=sys.stderr,
        )
        return REFUSED
    if out.exit != OK:
        return _refused(out)
    if out.data.get("pr"):
        for w in out.data["warnings"]:
            print(f"  {w}", file=sys.stderr)
        stacked = f", stacked on {out.data['stacked_on']}" if out.data["stacked_on"] else ""
        c.out(
            f"{'opened' if out.data['created'] else 'updated'} {out.data['pr']} into "
            f"{out.data['base']}{stacked}. {a.id} is IN REVIEW and its lease is released — "
            f"take the next item; `ddflow pr sync` completes it once merged.",
            out.body(MERGE_PAYLOAD),
        )
        return OK
    if out.data["kept_reason"]:
        print(f"  {out.data['kept_reason']}", file=sys.stderr)
    outside = out.data.get("outside_globs") or []
    if outside:
        print(
            f"  WARNING: {len(outside)} landed path(s) outside {a.id}'s globs -- another "
            f"item's work on the same branch?",
            file=sys.stderr,
        )
        for p in outside[:MAX_LISTED_FILES]:
            print(f"    {p}", file=sys.stderr)
    for extra in out.data["back_merged"]:
        print(f"  back-merged into {extra}", file=sys.stderr)
    if out.data.get("export_refresh"):
        print(f"  {out.data['export_refresh']['summary']}", file=sys.stderr)
    ci = out.data.get("ci")
    if ci:  # the base's health after the landing ([ci].on_merge)
        said = {"passed": "passed", "failed": "FAILED", "unavailable": "could not run"}.get(
            ci["status"], ci["status"]
        )
        extra = f": {', '.join(ci['failed'])}" if ci.get("failed") else ""
        print(f"  ci on {out.data['base']}: {said}{extra}", file=sys.stderr)
        for bug in ci.get("bugs", []):
            task = ci.get("fix_tasks", {}).get(bug)
            print(
                f"    bug {bug} filed" + (f" ({task} is queued)" if task else ""), file=sys.stderr
            )
    c.out(
        f"merged {a.id} ({out.data['sha'][:8]}) into {out.data['base']}",
        out.body(MERGE_PAYLOAD + tuple(k for k in ("export_refresh", "ci") if k in out.data)),
    )
    return OK


def _shell_cwd() -> Path | None:
    """The directory this process -- and so the caller's shell -- stands in, if any."""
    try:
        return Path.cwd()
    except OSError:
        return None


#: The wire body of `merge` on both surfaces. `pr` is empty for a local merge. `sha` is
#: the landing on `base`; `branch_head` the merged branch's own head.
MERGE_PAYLOAD = (
    "id",
    "sha",
    "branch_head",
    "base",
    "pr",
    "branch",
    "outside_globs",
    "worktree",
    "worktree_removed",
)


def cmd_brief(a, c: Ctx) -> int:
    out = A.brief(
        c.repo,
        item=a.item or "",
        phase=a.phase or "",
        check_recovery=a.check_recovery,
        agent=c.requested_agent,
    )
    if out.exit == FAIL:  # an unknown --phase (Bc2acd426f4)
        return _next_without_plan(out, c)
    if c.json:
        print(json.dumps(out.body(("brief", "item", "ready", "approx_tokens")), indent=2))
    else:
        print(out.data["text"])
    return OK
