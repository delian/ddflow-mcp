"""The item-edit machinery: `ItemEdit`, `update`, and the update/rebind path.

`ItemEdit` is the fields `update` may change (a record rather than keywords), and
`plan_edit` is the same computation, pure, for `batch` to fold into an edit before
recording any. `update` also rebinds an item to a worktree (`--worktree`) and retargets
a live lease when its globs or resources change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import ddflow.api.item_moves as IM
from ddflow.api import items as _items

from ...core import globspec as GS
from ...core import outcome as O
from ...core import schedule as SCH
from ...core.flow import line_order
from ...core.globs import overlap as globs_overlap
from ...infra import worktree as W
from ...services import items as IT
from ...services import landed as LD
from ...services import leases as L
from ...services import waiting as WG
from ..lifecycle.claim import _worktree_held_by


@dataclass
class ItemEdit:
    """The fields `update` may change. `None` = leave alone; `[]` / `""` = clear.

    A record rather than keywords: release lines and physical resources were added to
    `update` by two branches at once, and the keyword list passed the argument limit --
    the same pressure `LessonDraft` and `decisions.Draft` answer the same way.
    """

    title: str | None = None
    body: str | None = None
    #: Text added after the item's current body (or after ``body`` when given too), so a
    #: note never means re-sending, and risking dropping, the original (`--append-body`).
    append_body: str | None = None
    needs: list[str] | None = None
    #: Ids added to / taken from the item's current needs (or from ``needs`` when given too).
    add_needs: list[str] | None = None
    remove_needs: list[str] | None = None
    #: Accept needs that name no id in the queue (an id that will exist later, or a
    #: `repo:ID` dependency): --allow-unknown-needs. Off, the edit refuses an unknown id.
    allow_unknown_needs: bool = False
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    globs: list[str] | None = None
    #: Globs added to / taken from the item's current ones (or from ``globs`` when that is
    #: given too), so widening a lease never repeats the whole list (`--add-globs`).
    add_globs: list[str] | None = None
    remove_globs: list[str] | None = None
    tags: list[str] | None = None
    priority: int | None = None
    line: str | None = None
    resources: list[str] | None = None
    #: Rebind the item to this linked worktree and its branch (D-sticky-binding-remedy).
    worktree: str | None = None
    #: Keep a refused widening in the first-in-first-out line for its files (`--queue`).
    queue: bool = False
    #: Move the task under this phase (or task); "" makes it top level. `None` leaves it.
    parent: str | None = None
    #: Change a task's kind to another of `WORK_KINDS`, and so its gate pipeline.
    kind: str | None = None
    #: A commit (or ``<before>..<after>``) already on the base that holds this item's work,
    #: for work merged outside `ddflow merge` (B9eb92adafe). Verified, then recorded as the
    #: item's landed range.
    landed: str | None = None
    #: The item's own paths inside the landing (glob patterns), for work that landed inside
    #: a MIXED commit (B738d7bbeab): each verified inside the item's declared globs and
    #: inside ``--landed``'s changed files, then recorded; `changed_paths` reports only
    #: those. May be recorded alone later, against the range already on record.
    landed_paths: list[str] | None = None
    #: ``field=value`` items: a record of another kind's own fields (a lesson's ``why``, a
    #: bug's ``severity``) and, over MCP, ``add_tags=a,b`` style list edits (DX-update-universal).
    set: list[str] | None = None


def update(repo: Path, item: str, edit: ItemEdit, *, agent: str = "") -> O.Outcome:
    """Change an item's fields. `None` means "leave alone"; `[]` means "clear".

    That sentence is the whole reason this function exists. Over argv the two collapse
    into one empty string, and the surface had to carry a `clearable` flag to tell them
     apart — a distinction the type system expresses for free.
    """
    log, cfg, st = _items._load(repo, agent)
    # Planned from a fold taken INSIDE the lock (B518cb79124): `plan_edit`'s coordination
    # checks -- a line move for claimed or forked work, a parent move under a claimed
    # item, a dependency cycle -- must judge the log as it is when the edit appends. A
    # concurrent claim or move between the caller's snapshot and the append is refused
    # here instead of recorded against a state that no longer is.
    with log.transaction():
        st = L._fold(log)
        it = st.items.get(item)
        if it is None:
            from ..record_update import update_record

            other = update_record(repo, item, edit, agent=agent)
            if other is not None:
                return other
        if it is None or it.removed:
            gone = " (it was removed from the queue)" if it is not None else ""
            return O.failed("item.updated", f"no such item {item!r}{gone}", id=item)
        fields = plan_edit(cfg, st, it, edit)
        if isinstance(fields, O.Outcome):
            if not (
                (edit.landed is not None or edit.landed_paths is not None)
                and fields.exit == O.NOTHING
            ):
                return fields
            fields = {}
        if edit.landed is not None or edit.landed_paths is not None:
            # Checked against the globs this same edit leaves the item with.
            it2 = replace(it, globs=fields.get("globs", it.globs))
        if edit.landed is not None:
            landed = _landed_fields(repo, cfg, it2, edit.landed)
            if isinstance(landed, O.Outcome):
                return landed
            fields.update(landed)
        if edit.landed_paths is not None:
            restriction = _landed_paths_fields(repo, cfg, it2, edit.landed_paths, fields)
            if isinstance(restriction, O.Outcome):
                return restriction
            fields.update(restriction)
        if edit.worktree is not None:
            return _update_and_rebind(repo, log, cfg, item, fields, edit.worktree, edit.queue)
        return _record_update(log, cfg, it, fields, queue=edit.queue)


def _landed_fields(repo: Path, cfg, it, spec: str) -> dict[str, Any] | O.Outcome:
    """``landed_before``/``landed_after`` for ``--landed``, verified, or the refusal."""
    if it.landed_before and it.landed_after:
        return O.refused("item.updated", f"{it.id} already has a landed range", id=it.id)
    got = LD.parse(repo, spec)
    if isinstance(got, str):
        return O.failed("item.updated", f"{it.id}: {got}", id=it.id)
    base = it.base or cfg.worktree.base_ref or W.default_branch(repo)
    bad = LD.check(repo, it, base, *got)
    if bad:
        return O.failed("item.updated", f"{it.id}: --landed {spec}: {bad}", id=it.id)
    return {"landed_before": got[0], "landed_after": got[1]}


def _landed_paths_fields(
    repo: Path, cfg, it, paths: list[str], fields: dict[str, Any]
) -> dict[str, Any] | O.Outcome:
    """``landed_paths`` for ``--landed-paths``, verified against the item's landed range --
    the one ``--landed`` records in the same update, or the one already on record -- or the
    refusal. Recordable AGAIN to correct a wrong restriction (re-verified the same way),
    and cleared with an empty value (roborev: a one-way restriction poisons the
    pipeline forever)."""
    before = fields.get("landed_before") or it.landed_before
    after = fields.get("landed_after") or it.landed_after
    if not before or not after:
        return O.refused(
            "item.updated",
            f"{it.id}: --landed-paths needs a landing to restrict: record one with "
            "`ddflow update <id> --landed <sha>`, or pass both together",
            id=it.id,
        )
    paths = [p for p in paths if p]
    if not paths:
        if not it.landed_paths:
            return O.nothing("item.updated", f"{it.id} has no path restriction to clear", id=it.id)
        # Empty = clear: a recorded restriction can always be taken back.
        return {"landed_paths": []}
    if paths == it.landed_paths:
        return O.nothing(
            "item.updated", f"{it.id} already restricts its landing to those paths", id=it.id
        )
    bad = LD.check_paths(repo, it, before, after, paths)
    if bad:
        return O.failed(
            "item.updated", f"{it.id}: --landed-paths {', '.join(paths)}: {bad}", id=it.id
        )
    return {"landed_paths": paths}


def plan_edit(cfg, st, it, edit: ItemEdit) -> dict[str, Any] | O.Outcome:
    """The fields ``edit`` would record on ``it`` -- or the refusal, failure or "nothing to
    change" outcome, with nothing appended. Pure: `update` records the result, and `batch`
    plans many edits before recording any (DX-task-fold)."""
    if edit.line:
        bad = _bad_line(cfg, edit.line)
        if bad:
            return O.failed("item.updated", bad, id=it.id)
    if edit.line is not None and edit.line != it.line:
        moved = _line_frozen(st, it)
        if moved:
            return O.refused("item.updated", moved, id=it.id)

    fields = _edit_fields(edit)
    if edit.append_body:
        base = it.body if edit.body is None else edit.body
        fields["body"] = (base + "\n\n" if base else "") + edit.append_body
    # Every fold runs (no short-circuit: a no-op needs fold must not swallow a tags edit);
    # it is "nothing to change" only when nothing else was edited and no fold moved a list.
    _adjust_list(it, edit, fields, "needs")
    _adjust_list(it, edit, fields, "tags")
    _adjust_globs(it, edit, fields)
    # Validate only the ids THIS edit introduces -- a replacement (`--needs`) or added ones
    # (`--add-needs`) -- not the merged result: an item filed before the fix may already
    # hold an unknown need, and a valid `--add-needs REAL` must not be refused on it.
    introduced = (edit.needs or []) + (edit.add_needs or [])
    if not edit.allow_unknown_needs and (bad := IT.unknown_needs(st, introduced)):
        return O.failed("item.updated", f"{it.id}: {bad}", id=it.id)
    moved = IM.plan(cfg, st, it, edit.parent, edit.kind, _line_frozen, fields.get("needs"))
    if isinstance(moved, str):
        make = O.refused if isinstance(moved, IM.Refusal) else O.failed
        return make("item.updated", f"{it.id}: {moved}", id=it.id)
    fields.update(moved)
    unchanged = _nothing_to_change(it, edit, fields)
    if unchanged:
        return O.nothing("item.updated", unchanged, id=it.id)
    bad = GS.problem(fields.get("globs", [])) or SCH.problem(fields.get("resources", []))
    if bad:
        return O.failed("item.updated", f"{it.id}: {bad}", id=it.id)
    if not fields and edit.worktree is None:
        return O.nothing(
            "item.updated",
            "nothing to change: every field was left unset. Pass a value to set one, "
            "or an empty list to clear it.",
            id=it.id,
        )
    return fields


def _held_globs(it) -> list[str]:
    """The globs an item holds: its own and its lease's, without repeats."""
    return list(dict.fromkeys([*it.globs, *(it.lease.globs if it.lease else [])]))


def _adjust_list(it, edit: ItemEdit, fields: dict[str, Any], name: str) -> None:
    """Fold ``--add-<name>`` / ``--remove-<name>`` (needs, tags) into ``fields[name]``: onto
    ``--<name>`` when given, else onto the item's current list."""
    add, rem = getattr(edit, f"add_{name}"), getattr(edit, f"remove_{name}")
    if add is None and rem is None:
        return
    held = fields.get(name)
    if held is None:
        held = list(getattr(it, name))
    fields[name] = GS.adjust(held, add, rem)


def _nothing_to_change(it, edit: ItemEdit, fields: dict[str, Any]) -> str:
    """Why the edit is a no-op when it only asked to add/remove what changed no list."""
    folded = [
        n
        for n in ("needs", "tags", "globs")
        if getattr(edit, f"add_{n}") is not None or getattr(edit, f"remove_{n}") is not None
    ]
    if not folded or set(fields) != set(folded):
        return ""
    for n in folded:
        if getattr(edit, n) is not None:
            return ""
        held = _held_globs(it) if n == "globs" else list(getattr(it, n))
        if fields[n] != held:
            return ""
    return (
        "nothing to change: "
        + " and ".join(f"--add-{n}/--remove-{n}" for n in folded)
        + " add nothing new and remove nothing held."
    )


def _adjust_globs(it, edit: ItemEdit, fields: dict[str, Any]) -> None:
    """Fold ``--add-globs`` / ``--remove-globs`` into ``fields["globs"]``: onto ``--globs``
    when that was given, else onto the item's held globs (its own and its lease's)."""
    if edit.add_globs is None and edit.remove_globs is None:
        return
    held = fields.get("globs")
    if held is None:
        held = _held_globs(it)
    fields["globs"] = GS.adjust(held, edit.add_globs, edit.remove_globs)


def _update_and_rebind(
    repo: Path, log, cfg, item: str, fields: dict, path: str, queue: bool = False
) -> O.Outcome:
    """Set ``fields`` and rebind ``item`` to ``path``, all or nothing, under ONE lock.

    Decided from a fold taken INSIDE the lock: from the caller's earlier snapshot, a
    lease of its own that lapsed while another agent claimed the item read as its own,
    and the rebind re-acquired over the new holder (critic). And no write lands unless
    every one does: a refused field edit rebinds nothing, a refused rebind sets no field.
    """
    with log.transaction():
        st = L._fold(log)
        it = st.items[item]
        target = _rebind_target(repo, cfg, st, it, path, log.agent_id)
        if isinstance(target, O.Outcome):
            return target
        if fields:
            out = _record_update(log, cfg, it, fields, queue=queue)
            if out.exit != O.OK:
                return out
        else:
            out = O.ok("item.updated", id=it.id, changed=[], fields={})
        out.data.update(_rebind(log, cfg, it, *target))
        out.data["changed"] = sorted([*out.data["changed"], "worktree"])
        return out


def _rebind_target(repo: Path, cfg, st, it, path: str, me: str):
    """(stored path, branch) to rebind ``it`` to, or the refusal.

    The way out of a WRONG binding (Bec8d5228c9): an item adopted into somebody else's
    tree re-bound to it on every claim, and `merge` landed that tree's empty branch.
    Refused for anything that is not a linked worktree of this repository with a branch
    checked out, for a tree another open item is bound to (two items in one tree cannot
    be merged or recovered apart), and for an item whose LIVE lease another agent holds
    -- moving someone's work out from under them is theirs to do.
    """

    if not path.strip():
        # Not "here": an empty `$WT` would otherwise rebind to wherever the shell is.
        return O.refused("item.updated", "--worktree is empty: name the tree.", id=it.id)
    where = Path(path).expanduser()
    if not where.is_absolute():
        where = repo / where
    here = W.current(where) if where.is_dir() else None
    try:
        same_repo = here is not None and W.repo_root(where) == W.repo_root(repo)
    except W.GitError:
        same_repo = False
    if here is None or not same_repo:  # `W.current` is None for a detached HEAD too
        return O.refused(
            "item.updated",
            f"{path} is not a linked worktree of this repository on a branch (the primary "
            f"checkout is nobody's tree in particular). Name the worktree {it.id}'s work "
            f"is in, with its branch checked out.",
            id=it.id,
        )
    stored = W.store_path(repo, here.path)
    held = _worktree_held_by(st, stored, it.id, repo, cfg)
    if held:
        return O.refused(
            "item.updated",
            f"{here.path} is bound to {held}, which is still open. Two items sharing one "
            f"tree cannot be merged or recovered separately.",
            id=it.id,
            conflicts_with=held,
        )
    lease = it.lease
    live = lease is not None and lease.live(time.time(), cfg.lease.grace_s)
    if live and lease.holder != me:
        return O.refused(
            "item.updated",
            f"{it.id} is held by {lease.holder}; only the holder rebinds its tree.",
            id=it.id,
            holder=lease.holder,
        )
    return stored, here.branch


def _rebind(log, cfg, it, stored: str, branch: str) -> dict[str, str]:
    """Bind ``it`` -- and the caller's live lease on it -- to ``stored`` on ``branch``.

    Called under the log lock with ``it`` folded inside it, after `_rebind_target`
    refused any live lease held by someone else: a live lease here is the caller's, and
    `L.acquire` (which returns a Lease or raises) takes its renew-in-place path, which
    neither checks nor refuses anything -- so after the field edit, nothing here can
    fail half-way. Recorded as `worktree.adopted`, as a claim that adopts a tree is:
    ddflow did not make it, so `merge` never removes it.
    """

    lease = it.lease
    if lease is not None and lease.live(time.time(), cfg.lease.grace_s):
        L.acquire(log, cfg, it.id, worktree=stored, branch=branch, force=True)
    log.append("worktree.adopted", it.id, {"path": stored, "branch": branch, "base": ""})
    return {"worktree": stored, "branch": branch}


def _edit_fields(edit: ItemEdit) -> dict[str, Any]:
    """The fields ``edit`` sets -- each one not left at None -- as the event records them.

    Globs are read by `globspec`, as everywhere else: an element may be a comma list or a
    JSON array, and either is taken whole.
    """
    fields: dict[str, Any] = {}
    if edit.title is not None:
        fields["title"] = edit.title
    if edit.body is not None:
        fields["body"] = edit.body
    if edit.needs is not None:
        fields["needs"] = list(edit.needs)
    if edit.globs is not None:
        fields["globs"] = GS.parse(edit.globs)
    if edit.tags is not None:
        fields["tags"] = list(edit.tags)
    if edit.resources is not None:
        fields["resources"] = list(edit.resources)
    if edit.priority is not None:
        fields["priority"] = int(edit.priority)
    if edit.line is not None:
        fields["line"] = edit.line
    return fields


def _record_update(log, cfg, it, fields: dict[str, Any], *, queue: bool = False) -> O.Outcome:
    """Append the edit, and retarget the item's live lease when its globs or resources
    change.

    A claim's globs and resources live on its LEASE, which the commit hook, the conflict
    checks and the capacity check read -- so new values on a claimed item go to the lease
    too. Decided and appended under one lock, and a refusal (the new globs overlap another
    agent's live lease, or the new resources exceed what is free) records NOTHING: not the
    edit, not the lease event.
    """
    globs = fields.get("globs")
    resources = fields.get("resources")
    with log.transaction():
        lease, refusal, accepted = (
            L.plan_retarget(log, cfg, it.id, globs, resources, keep_partial=True)
            if globs is not None or resources is not None
            else (None, "", None)
        )
        if refusal:
            return O.refused(
                "item.updated", refusal + _waiting(log, cfg, it, lease, globs, queue), id=it.id
            )
        withheld = (
            [g for g in globs if g not in accepted]
            if globs is not None and accepted is not None
            else []
        )
        if withheld:
            # Widening that only partly overlaps another lease takes the rest
            # (B-ddflow-glob-widen-refused-broad): the clashing globs are held back, the
            # accepted subset becomes the item's and the lease's globs, and the withheld
            # ones are registered as waiting so the holder is woken when they free up.
            fields["globs"] = accepted
            _waiting(log, cfg, it, lease, withheld, queue)
        log.append(f"{it.family}.updated", it.id, fields)
        if lease is not None:
            L.retarget(log, it.id, lease, accepted, resources)
            _withdraw_stale_wait(log, it, lease, globs, withheld)
    overtook = (
        _overtaken_waiters(log, cfg, _held_globs(it), accepted)
        if globs is not None and lease is not None
        else []
    )
    return O.ok(
        "item.updated",
        id=it.id,
        changed=sorted(fields),
        fields=fields,
        lease_retargeted=lease is not None,
        globs_dropped=_dropped(it, accepted),
        globs_withheld=withheld,
        globs_overtook=overtook,
        needs_dropped=[n for n in it.needs if n not in fields["needs"]]
        if "needs" in fields
        else [],
        tags_dropped=[n for n in it.tags if n not in fields["tags"]] if "tags" in fields else [],
    )


def _withdraw_stale_wait(log, it, lease, globs: list[str] | None, withheld: list[str]) -> None:
    """Remove a stale prior wait after a successful glob retarget.

    A successful `--globs` retarget is the caller deciding the ask differently: a refused
    widening made earlier is abandoned, or `standing` reads it as a cycle forever. A
    just-`withheld` ask is not stale -- the caller still wants those files -- so it stays
    (B-update-queue-wait-not-withdrawable).
    """
    if globs is None or withheld:
        return
    waiting = L._fold(log).waiting.get(it.id)
    if waiting and waiting.get("globs"):
        WG.withdraw(log, it.id, lease.holder, lease.event, time.time())


def _overtaken_waiters(
    log, cfg, old_globs: list[str], accepted: list[str] | None
) -> list[dict[str, Any]]:
    """The standing waiters a just-applied widening overtook (B-widen-over-standing-wait-silent).

    Only a waiter is "overtaken" that a NEWLY-granted glob now blocks and that the item's
    OLD globs did not already block for that same ask: a waiter already waiting on a held
    glob, or one a wider glob would only re-state, is not. ``new_globs`` is ``accepted``
    minus the exact held strings; the overlap test below then drops a "new" directory that
    merely subsumes a held file the waiter had already asked for."""
    if accepted is None:
        return []
    new_globs = [g for g in accepted if g not in old_globs]
    if not new_globs:
        return []
    waiters = WG.waiters(L._fold(log), cfg, time.time())
    blocked_before = {
        w.item for w in waiters if any(globs_overlap(g, x) for g in old_globs for x in w.globs)
    }
    return [
        {"item": w.item, "holder": w.holder, "globs": list(w.globs)}
        for w in waiters
        if w.item not in blocked_before
        and any(globs_overlap(g, x) for g in new_globs for x in w.globs)
    ]


def _waiting(log, cfg, it, lease, globs: list[str] | None, queue: bool) -> str:
    """Record a refused widening as waiting (it returns at once; the lease is kept) and, once
    the wait outlasts `[lease].wait_max_s`, name the own globs others are waiting for.
    "" when nothing was refused for a file another lease holds."""
    if lease is None or globs is None:
        return ""
    now = time.time()
    state = L._fold(log)
    WG.register(log, cfg, state, it.id, lease.holder, globs, now, queue=queue, lease=lease)
    advice = WG.release_advice(L._fold(log), cfg, it.id, now)
    return WG.advice_text(it.id, advice, list(lease.globs)) if advice else ""


def _dropped(it, globs: list[str] | None) -> list[str]:
    """What new ``globs`` take away from the item and its lease; [] when left alone.

    `--globs` replaces the list (a field set, like every other `update` field), so an
    agent widening a claim with only its new paths drops the old ones from every conflict
    check. That is legitimate when meant -- and invisible when not, so it is reported.
    """
    if globs is None:
        return []
    return [g for g in _held_globs(it) if g not in globs]


def _line_frozen(st, it) -> str:
    """Why ``it``'s line may not change now, or "".

    Ports were planned from the lines as they were: moving a fix (or a port) re-aims a
    forward-merge at another branch -- a whole newer major merged into an old line, found
    by review. And an item already forked has a branch built on its old line's base;
    merging it into another line carries that base's history along.
    """

    ports = [o.id for o in st.items.values() if o.port_from == it.id and not o.removed]
    if it.port_from or ports:
        return (
            f"{it.id} is part of a planned port ({', '.join(ports) or 'from ' + it.port_from}); "
            f"its lines were fixed when the port was planned. Abandon and re-file it with "
            f"the lines you want."
        )
    if it.worktree or it.branch or it.terminal or it.lease:
        return (
            f"{it.id} already has a branch forked from its current line's base; moving it "
            f"would merge that history into another line. Abandon and re-file it on the "
            f"line you want."
        )
    return ""


def _bad_line(cfg, line: str) -> str:
    """Why ``line`` names no release line, or "". A typo'd line must not silently mean
    'the current line' -- the fix would land on the wrong major and nothing would say so."""

    known = line_order(cfg)
    if line in known:
        return ""
    if not cfg.flow.lines:
        return (
            f"no release lines are configured, so --line {line!r} names nothing. Add "
            f"maintenance lines under [flow.lines] (oldest first), e.g. "
            f'`"2" = "maint/2.x"`.'
        )
    return f"unknown release line {line!r}. Lines, oldest first: {', '.join(known)}"
