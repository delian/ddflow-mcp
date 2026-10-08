"""Creating and changing queue items."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ..config import csv_list
from ..core import globspec as GS
from ..core import ids as IDS
from ..core import outcome as O
from ..core.model import fold
from ..services import items as IT
from ..services import leases as L
from ._base import _load


@dataclass
class ItemEdit:
    """The fields `update` may change. `None` = leave alone; `[]` / `""` = clear.

    A record rather than keywords: release lines and physical resources were added to
    `update` by two branches at once, and the keyword list passed the argument limit --
    the same pressure `LessonDraft` and `decisions.Draft` answer the same way.
    """

    title: str | None = None
    body: str | None = None
    needs: list[str] | None = None
    globs: list[str] | None = None
    tags: list[str] | None = None
    priority: int | None = None
    line: str | None = None
    resources: list[str] | None = None
    #: Rebind the item to this linked worktree and its branch (D-sticky-binding-remedy).
    worktree: str | None = None


def update(repo: Path, item: str, edit: ItemEdit, *, agent: str = "") -> O.Outcome:
    """Change an item's fields. `None` means "leave alone"; `[]` means "clear".

    That sentence is the whole reason this function exists. Over argv the two collapse
    into one empty string, and the surface had to carry a `clearable` flag to tell them
    apart — a distinction the type system expresses for free.
    """
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("item.updated", f"no such item {item!r}{gone}", id=item)
    if edit.line:
        bad = _bad_line(cfg, edit.line)
        if bad:
            return O.failed("item.updated", bad, id=item)
    if edit.line is not None and edit.line != it.line:
        moved = _line_frozen(st, it)
        if moved:
            return O.refused("item.updated", moved, id=item)

    fields = _edit_fields(edit)
    bad = GS.problem(fields.get("globs", []))
    if bad:
        return O.failed("item.updated", f"{item}: {bad}", id=item)
    if not fields and edit.worktree is None:
        return O.nothing(
            "item.updated",
            "nothing to change: every field was left unset. Pass a value to set one, "
            "or an empty list to clear it.",
            id=item,
        )
    if edit.worktree is not None:
        return _update_and_rebind(repo, log, cfg, item, fields, edit.worktree)
    return _record_update(log, cfg, it, fields)


def _update_and_rebind(repo: Path, log, cfg, item: str, fields: dict, path: str) -> O.Outcome:
    """Set ``fields`` and rebind ``item`` to ``path``, all or nothing, under ONE lock.

    Decided from a fold taken INSIDE the lock: from the caller's earlier snapshot, a
    lease of its own that lapsed while another agent claimed the item read as its own,
    and the rebind re-acquired over the new holder (critic). And no write lands unless
    every one does: a refused field edit rebinds nothing, a refused rebind sets no field.
    """
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        it = st.items[item]
        target = _rebind_target(repo, cfg, st, it, path, log.agent_id)
        if isinstance(target, O.Outcome):
            return target
        if fields:
            out = _record_update(log, cfg, it, fields)
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

    from ..infra import worktree as W
    from .lifecycle import _worktree_held_by

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


def _record_update(log, cfg, it, fields: dict[str, Any]) -> O.Outcome:
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
        lease, refusal = (
            L.plan_retarget(log, cfg, it.id, globs, resources)
            if globs is not None or resources is not None
            else (None, "")
        )
        if refusal:
            return O.refused("item.updated", refusal, id=it.id)
        log.append(f"{it.kind}.updated", it.id, fields)
        if lease is not None:
            L.retarget(log, it.id, lease, globs, resources)
    return O.ok(
        "item.updated",
        id=it.id,
        changed=sorted(fields),
        fields=fields,
        lease_retargeted=lease is not None,
        globs_dropped=_dropped(it, globs),
    )


def _dropped(it, globs: list[str] | None) -> list[str]:
    """What new ``globs`` take away from the item and its lease; [] when left alone.

    `--globs` replaces the list (a field set, like every other `update` field), so an
    agent widening a claim with only its new paths drops the old ones from every conflict
    check. That is legitimate when meant -- and invisible when not, so it is reported.
    """
    if globs is None:
        return []
    before = [*it.globs, *(it.lease.globs if it.lease else [])]
    return [g for g in dict.fromkeys(before) if g not in globs]


def _fresh(log):
    """The state to decide an add from, read UNDER the append lock: two agents filing the
    same id at once must not both see it free."""
    return fold(log.read_all(), strict=False)


#: Splitting into one piece is a rename, not a split.
MIN_SPLIT_PARTS = 2

#: Where an item sits when nobody says otherwise. The MIDDLE of the range, so a later
#: item can be pushed either way without renumbering anything.
#:
#: Declared here and imported by the parser, not written twice. Duplicated, it became
#: 100 in argparse and 0 in this layer — so every phase and task created over MCP was
#: filed at the TOP priority while the CLI filed them in the middle, and nothing said so.
DEFAULT_PRIORITY = 100


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
    from ..core.flow import line_order

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


def phase_add(  # noqa: PLR0913 -- BACKLOG B179: the same draft record as task_add
    repo: Path,
    item: str,
    *,
    title: str = "",
    needs: str = "",
    globs: str = "",
    body: str = "",
    tags: str = "",
    priority: int = DEFAULT_PRIORITY,
    line: str = "",
    readd: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """Add a phase — an umbrella that completes when its tasks do.

    ``line`` puts the whole phase on a release line; its tasks inherit it. An id already
    in the queue is REFUSED; ``readd`` lets a removed one come back. A phase that reads
    like an existing record is refused until ``answer`` says what it is (``_dedupe``).
    """
    bad = IT.bad_id(item) or GS.problem(GS.parse(globs))
    if bad:
        return O.failed("phase.added", bad, id=item)
    log, cfg, st = _load(repo, agent)
    bad = _bad_line(cfg, line) if line else ""
    if bad:
        return O.failed("phase.added", bad, id=item)
    with log.transaction():
        st = _fresh(log)
        taken = IT.taken(st, item, readd=readd)
        if taken:
            return O.refused("phase.added", taken, id=item)
        # Inside the transaction, against the log as it is NOW: a record another agent
        # filed since `_load` is seen.
        chk = DD.check_add(
            repo,
            log,
            cfg,
            st,
            DD.Record(kind="phase", event_kind="phase.added", rid=item, title=title, body=body),
            answer,
        )
        if chk.refusal is not None:
            return chk.refusal
        if chk.extension:
            return DD.extend(log, cfg, chk, "phase.added")
        log.append(
            "phase.added",
            item,
            {
                "title": title,
                "needs": csv_list(needs),
                "globs": GS.parse(globs),
                "body": body,
                "tags": csv_list(tags),
                "priority": priority,
                "line": line,
                **chk.fields,
            },
        )
        DD.after_add(log, cfg, item, chk)
    return O.ok("phase.added", id=item, **chk.data())


def _port_of_lines(st, cfg, port_of: str, wanted: list[str]) -> tuple[list[str], str]:
    """The lines a follow-up to ``port_of`` must reach (B180), or the reason it cannot."""
    from ..core import flow as F

    if wanted:
        return wanted, "--port-of takes its lines from that fix: drop --line/--lines"
    origin = st.items.get(port_of)
    if origin is None:
        return [], f"--port-of: no such item {port_of!r}"
    if origin.removed:
        return [], f"--port-of: {origin.id!r} was removed"
    origin = st.items.get(origin.port_of) or origin
    if origin.removed:
        return [], f"--port-of: {origin.id!r} was removed"
    family = [origin, *(o for o in st.items.values() if o.port_of == origin.id and not o.removed)]
    lines = (F.effective_line(st, o) or cfg.flow.current_line for o in family)
    return list(dict.fromkeys(lines)), ""


def task_add(  # noqa: PLR0913 -- BACKLOG B179: a TaskDraft record, as decisions have
    repo: Path,
    item: str,
    *,
    title: str = "",
    parent: str = "",
    needs: str = "",
    globs: str = "",
    body: str = "",
    tags: str = "",
    priority: int = DEFAULT_PRIORITY,
    line: str = "",
    lines: str = "",
    port_of: str = "",
    readd: bool = False,
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """Add a task. Its parent may be a phase OR another task (making it a sub-task).

    ``line`` puts it on one release line. ``lines`` (comma-separated) files a fix that
    must reach SEVERAL: the task is written on the line `port_strategy` dictates and a
    port item is generated for each other line -- `<id>@<line>`, an ordinary task with
    its own branch, gates and merge, which starts once what it carries has landed.
    ``port_of`` names an earlier fix: this is a FOLLOW-UP to it and takes the lines that
    fix reached (B180), so its ports carry what THIS one lands. Naming one of the earlier
    fix's ports resolves to the fix. It cannot be combined with ``line``/``lines``.

    Tasks can be added at ANY time, including while their parent is being worked: a task
    that turns out to contain two things is the normal case, not an exception, and a
    queue that cannot absorb that discovery pushes the work into someone's head.

    An id already in the queue is REFUSED: adding never changes an item, `update` does.
    ``readd`` lets a REMOVED id come back with the new definition.

    A task that reads like an existing record is REFUSED until ``answer`` says what it is
    (``api._dedupe``): ``new``, or ``extends`` / ``duplicate_of`` / ``related`` a record.
    ``answer`` is one bundled parameter, not three (BACKLOG B179).
    """
    from ..core import flow as F
    from ..services import choices as CH

    base = IT.TaskDraft(
        item,
        parent=parent,
        globs=GS.parse(globs),
        body=body,
        tags=csv_list(tags),
        priority=priority,
    )
    bad = IT.problem_of(base)
    if bad:
        return O.failed("task.added", bad, id=item)
    log, cfg, st = _load(repo, agent)
    bad = IT.missing_parent(st, parent)
    if bad:
        return O.failed("task.added", bad, id=item)
    wanted = csv_list(lines) or ([line] if line else [])
    if port_of:
        wanted, bad = _port_of_lines(st, cfg, port_of, wanted)
        if bad:
            return O.failed("task.added", bad, id=item)
    for ln in wanted:
        bad = _bad_line(cfg, ln)
        if bad:
            return O.failed("task.added", bad, id=item)
    with log.transaction():
        st = _fresh(log)
        taken = IT.taken(st, item, readd=readd)
        if taken:
            return O.refused("task.added", taken, id=item)
        chk = DD.check_add(
            repo,
            log,
            cfg,
            st,
            DD.Record(
                kind="task", event_kind="task.added", rid=item, title=title, body=body, item=parent
            ),
            answer,
        )
        if chk.refusal is not None:
            return chk.refusal
        if chk.extension:
            return DD.extend(log, cfg, chk, "task.added")
        plan = None
        adopted: list[str] = []
        if len(set(wanted)) > 1:
            clash = [f"{item}@{ln}" for ln in wanted if f"{item}@{ln}" in st.items]
            if clash:
                return O.failed("task.added", f"{', '.join(clash)} already exist", id=item)
            # The first fix filed across lines is where the strategy starts to matter: an
            # unmade choice is defaulted HERE, on the record, and followed from now on.
            adopted = CH.adopt_defaults(log, cfg, ["port_strategy"])
            plan = F.plan_ports(cfg, wanted, cfg.flow.port_strategy)
        author_line = plan.author if plan else (wanted[0] if wanted else "")
        main = replace(base, title=title, needs=csv_list(needs), line=author_line)
        added = IT.add_task(log, st, cfg, main, dedupe=chk, readd=readd)
        if not added.ok:
            return (O.refused if added.refused else O.failed)("task.added", added.problem, id=item)
        DD.after_add(log, cfg, item, chk)
        ports: list[str] = []
        for ln, frm in plan.ports if plan else []:
            source = item if frm < 0 else ports[frm]
            pid = f"{item}@{ln}"
            port = replace(
                base,
                id=pid,
                title=f"{title or item} (port to {ln})",
                needs=[source],
                line=ln,
                tags=[*csv_list(tags), "port"],
                extra={
                    "port_of": item,
                    "port_from": source,
                    "port_strategy": plan.strategy,
                },
            )
            added = IT.add_task(
                log,
                st,
                cfg,
                port,
                dedupe="a port of the fix just checked: one per release line",
                readd=readd,
            )
            if not added.ok:
                return O.failed("task.added", added.problem, id=item)
            ports.append(pid)
        # Giving a task its first child turns it into an umbrella: its lease is released
        # (`split` does the same), see `IT.release_umbrella`.
        released = IT.release_umbrella(
            log, st, parent, note=f"became an umbrella when {item} was added"
        )
    return O.ok(
        "task.added",
        id=item,
        parent=parent,
        released_parent_lease=released,
        line=plan.author if plan else (wanted[0] if wanted else ""),
        ports=ports,
        port_strategy=plan.strategy if plan else "",
        port_note=plan.note if plan else "",
        defaulted=adopted,
        **chk.data(),
    )


def _contestants(records: list[dict[str, Any]], keep: str, who: str) -> list[dict[str, Any]]:
    """The records ``keep`` names: by event id, by agent or holder, or by an event-id
    prefix long enough not to be a guess."""
    exact = [r for r in records if keep in (r["event"], r[who])]
    if exact or len(keep) < _MIN_EVENT_PREFIX:
        return exact
    return [r for r in records if r["event"].startswith(keep)]


#: The shortest event-id prefix `resolve --keep` accepts. `e` plus five hex digits.
_MIN_EVENT_PREFIX = 6


def _options(it) -> str:
    rows = [f"{d['event']} ({d['agent']}: {d['title']!r})" for d in it.contested]
    rows += [
        f"{h['holder']} ({'current holder, ' if it.lease and h['event'] == it.lease.event else ''}"
        f"claim {h['event']})"
        for h in it.lease_candidates()
    ]
    return "; ".join(rows)


def resolve(repo: Path, item: str, *, keep: str, refile_as: str = "", agent: str = "") -> O.Outcome:
    """Settle a contested item: keep one definition and/or one claim, recorded as an event.

    B191. A merge can bring in a rival `<kind>.added` or a rival claim, and the fold
    records the contest instead of letting the later lamport silently win. This is the
    way out, and it is an EVENT (`item.resolved`, carrying what was kept) so every clone
    folds to the same answer. A losing definition is not dropped silently either:
    ``refile_as`` re-adds it under new ids (one per loser, in the order `show` lists
    them), and without it the result says how to. A losing CLAIM is released in the same
    transaction. An item that is not contested is refused.

    For a lease contest, ``keep`` may name ANY contestant, not only the one the fold
    displays: every claim that overlapped the kept one gets a `lease.released`, and so
    does the current holder if that is another claim; the resolution then reinstates the
    kept claim with its TTL running from now. A contestant that met neither lost nothing
    to the kept claim and is not released (bug Ba73ee6ea72). Keeping the current holder
    therefore records the claims it met as released; keeping a displaced one hands the
    item back to it. Either way one call settles the whole contest.

    ``keep`` may also name the current holder when it is not a contestant -- it took the
    item over after every contestant had ended -- and then every contestant is released
    and the item stays where it is (B-resolve-cannot-keep-holder).
    """

    log, cfg, _st = _load(repo, agent)
    with log.transaction():
        st = _fresh(log)
        it = st.items.get(item)
        if it is None or it.removed:
            gone = " (it was removed from the queue)" if it is not None else ""
            return O.failed("item.resolved", f"no such item {item!r}{gone}", id=item)
        if not it.contest_summary():
            return O.refused(
                "item.resolved",
                f"{item} is not contested: there is nothing to resolve. `ddflow update` "
                f"changes an item; `ddflow release` gives up a claim.",
                id=item,
            )
        defs = _contestants(it.contested, keep, "agent")
        claims = _contestants(it.lease_candidates(), keep, "holder")
        if not defs and not claims:
            return O.failed(
                "item.resolved",
                f"--keep {keep!r} names none of {item}'s contestants: {_options(it)}",
                id=item,
            )
        if len(defs) > 1 or len(claims) > 1:
            return O.failed(
                "item.resolved",
                f"--keep {keep!r} names more than one contestant; give an event id: {_options(it)}",
                id=item,
            )
        if defs and claims:
            # One token naming a definition AND a claim -- an agent that both filed and
            # claimed it, or a prefix of both ids -- would settle two separate questions
            # at once. Only a full event id says which one was meant.
            if keep == defs[0]["event"]:
                claims = []
            elif keep == claims[0]["event"]:
                defs = []
            else:
                return O.refused(
                    "item.resolved",
                    f"--keep {keep!r} names both a definition ({defs[0]['event']}) and a "
                    f"claim ({claims[0]['event']}) of {item}. Settle them one at a time: "
                    f"`--keep {defs[0]['event']}` keeps that definition, "
                    f"`--keep {claims[0]['event']}` keeps that claim.",
                    id=item,
                )
        lost = [d for d in it.contested if defs and d["event"] != defs[0]["event"]]
        new_ids = csv_list(refile_as)
        if new_ids and len(new_ids) != len(lost):
            return O.failed(
                "item.resolved",
                f"--refile-as gives {len(new_ids)} id(s) for {len(lost)} losing "
                f"definition(s) of {item}; give one per definition not kept.",
                id=item,
            )
        for nid in new_ids:
            bad = IT.bad_id(nid) or IT.taken(st, nid, readd=False)
            if bad:
                return O.failed("item.resolved", bad, id=item)
        losers = it.lease_losers(claims[0]) if claims else []
        data: dict[str, Any] = {"kind": it.kind, "keep": keep, "at": time.time()}
        if defs:
            data["definition"] = defs[0]
        if claims:
            data["claim"] = claims[0]
            # The TTL a kept claim that had lapsed runs its fresh window on: a recorded
            # expiry zeroed the claim's own, and a window of 0 s is dead on arrival.
            data["ttl_s"] = cfg.lease.ttl_s
        # Releases FIRST: folded before the resolution, each withdraws a losing claim,
        # and the resolution then re-applies the kept one whichever was displayed.
        for h in losers:
            L.release_claim(
                log,
                item,
                holder=h["holder"],
                event=h["event"],
                note=f"lost the contest for {item}: {claims[0]['holder']} keeps it",
            )
        log.append("item.resolved", item, data)
        for nid, d in zip(new_ids, lost if new_ids else [], strict=True):
            log.append(f"{it.kind}.added", nid, d["data"])
    # Outside the lock: each released claim's remote ref goes too, and the kept holder
    # takes the ref a displaced contestant did not hold (Bd45d1ad60e).
    L.settle_remote(
        log,
        cfg,
        item,
        dropped=[h["holder"] for h in losers],
        kept=claims[0]["holder"] if claims else "",
    )
    return O.ok(
        "item.resolved",
        id=item,
        item_kind=it.kind,
        kept_definition={k: v for k, v in defs[0].items() if k != "data"} if defs else None,
        kept_holder=claims[0]["holder"] if claims else "",
        lost=[{k: d[k] for k in ("event", "agent", "title", "body")} for d in lost],
        refiled=new_ids,
        released=[h["holder"] for h in losers],
    )


def split(
    repo: Path,
    item: str,
    *,
    into: list[str] | None = None,
    globs: str = "",
    needs: str = "",
    agent: str = "",
) -> O.Outcome:
    """Split an item into sub-tasks, in place, without losing its history.

    For the commonest discovery there is: a task turns out to be two things. The original
    stays put and becomes an umbrella — it keeps its id, its lease history and anything
    already recorded against it, and it completes when its children do.

    The alternative — closing the task and opening two new ones — loses the thread between
    the work that was planned and the work that happened, which is exactly what
    `ddflow replay` needs to reconstruct the project.
    """

    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("task.split", f"no such item {item!r}{gone}", id=item, created=[])
    bad = GS.problem(GS.parse(globs))
    if bad:
        return O.failed("task.split", bad, id=item, created=[])
    if it.terminal:
        return O.refused(
            "task.split",
            f"{item} is already {it.state}; splitting finished work would reopen it. "
            f"Add new tasks instead.",
            id=item,
            created=[],
        )
    specs = [x for x in (into or []) if x.strip()]
    if len(specs) < MIN_SPLIT_PARTS:
        return O.failed(
            "task.split",
            "--into must be given at least twice: splitting into one piece is not a "
            "split, it is a rename (`ddflow update <id> --title ...`).",
            id=item,
            created=[],
        )

    # Resolve and validate EVERY child before appending anything. The loop used to
    # validate and append in one pass, so a collision on the second `--into` exited
    # non-zero having already written the first: the parent became an umbrella nobody
    # asked for, un-claimable because it now had a child and un-completable because that
    # child was open. It also never compared the specs to each other, so
    # `--into X=one --into X=two` appended two `task.added` events for one id, `fold`
    # merged them, and the split reported two children while producing one whose title was
    # silently the second spec's.
    planned: list[tuple[str, str]] = []
    for i, spec in enumerate(specs, 1):
        sub_id, _, title = spec.partition("=")
        # the parent's own spelling: an existing id is not re-checked (render check=False)
        sub_id = sub_id.strip() or IDS.render(cfg, "split_child", check=False, parent=item, seq=i)
        if sub_id in st.items:
            return O.failed(
                "task.split", f"{sub_id} already exists; choose another id", id=item, created=[]
            )
        bad = IT.bad_id(sub_id)
        if bad:
            return O.failed("task.split", bad, id=item, created=[])
        if sub_id in [p for p, _ in planned]:
            return O.failed(
                "task.split",
                f"{sub_id} given twice in one split; each part needs its own id",
                id=item,
                created=[],
            )
        if sub_id == item:
            return O.failed(
                "task.split", f"{sub_id} cannot be its own sub-task", id=item, created=[]
            )
        planned.append((sub_id, title.strip() or f"{it.title} (part {i})"))

    created: list[str] = []
    for i, (sub_id, title) in enumerate(planned, 1):
        # Inherit the parent's globs unless the child declares its own: while the split is
        # half-done the children are the only things being worked, and a child with no
        # declared globs is a child the conflict detector cannot protect.
        draft = IT.TaskDraft(
            sub_id,
            parent=item,
            title=title,
            globs=GS.parse(globs) or list(it.globs),
            needs=csv_list(needs) if i == 1 else [],
            priority=it.priority,
        )
        added = IT.add_task(
            log, st, cfg, draft, dedupe="a part of the item being split: its text is the parent's"
        )
        if not added.ok:
            return O.failed("task.split", added.problem, id=item, created=created)
        created.append(sub_id)

    # The umbrella is no longer the thing being worked; holding its lease would block its
    # own children on a glob conflict with itself.
    IT.release_umbrella(log, st, item, note=f"split into {', '.join(created)}")
    log.append(
        "task.updated",
        item,
        {"body": (it.body + "\n\n" if it.body else "") + f"Split into: {', '.join(created)}."},
    )
    return O.ok("task.split", item=item, created=created, inherited_globs=not GS.parse(globs))
