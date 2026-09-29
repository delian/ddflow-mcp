"""Creating and changing queue items."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ..core.model import fold
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

    fields: dict[str, Any] = {}
    if edit.title is not None:
        fields["title"] = edit.title
    if edit.body is not None:
        fields["body"] = edit.body
    if edit.needs is not None:
        fields["needs"] = list(edit.needs)
    if edit.globs is not None:
        fields["globs"] = list(edit.globs)
    if edit.tags is not None:
        fields["tags"] = list(edit.tags)
    if edit.resources is not None:
        fields["resources"] = list(edit.resources)
    if edit.priority is not None:
        fields["priority"] = int(edit.priority)
    if edit.line is not None:
        fields["line"] = edit.line

    if not fields:
        return O.nothing(
            "item.updated",
            "nothing to change: every field was left unset. Pass a value to set one, "
            "or an empty list to clear it.",
            id=item,
        )
    return _record_update(log, cfg, it, fields)


def _record_update(log, cfg, it, fields: dict[str, Any]) -> O.Outcome:
    """Append the edit, and retarget the item's live lease when its globs change.

    A claim's globs live on its LEASE, which the commit hook and the conflict checks
    read -- so new globs on a claimed item go to the lease too. Decided and appended
    under one lock, and a refusal (the new globs overlap another agent's live lease)
    records NOTHING: not the edit, not the lease event.
    """
    with log.transaction():
        lease, refusal = (
            L.plan_retarget(log, cfg, it.id, fields["globs"]) if "globs" in fields else (None, "")
        )
        if refusal:
            return O.refused("item.updated", refusal, id=it.id)
        log.append(f"{it.kind}.updated", it.id, fields)
        if lease is not None:
            L.retarget(log, it.id, lease, fields["globs"])
    return O.ok(
        "item.updated",
        id=it.id,
        changed=sorted(fields),
        fields=fields,
        lease_retargeted=lease is not None,
    )


def _bad_id(item: str) -> str:
    """Why `item` cannot be a local id, or "". A colon is how a dependency names an item
    in ANOTHER repository (`repo:ID`); a local `foo:bar` was taken for one, looked up
    in the sibling observations, and blocked everything that needed it (rubber-duck)."""
    if ":" in item:
        return (
            f"{item!r}: a colon marks a dependency in another repository (`repo:ID`, "
            f"[schedule] repos), so it cannot be part of a local id"
        )
    return ""


def _taken(st, item: str, *, readd: bool) -> str:
    """Why ``item`` cannot be ADDED because the id is already in the queue, or "".

    An `added` event folds as a re-definition, so a second `task add B207` overwrote the
    first B207 -- title, body, globs -- while its lease and gate records stayed, now under
    another agent's title (B6bd279367e / B16042585f4). Adding never changes an item;
    `update` does. A REMOVED id may come back, but only when the caller says so.
    """
    it = st.items.get(item)
    if it is None:
        return ""
    what = f"{it.kind} {it.title!r}" if it.title else it.kind
    if it.removed:
        if readd:
            return ""
        return (
            f"{item} is a {what} that was removed from the queue. Re-adding it brings the "
            f"id back with the new definition: pass --readd (MCP/api: readd) to do that "
            f"on purpose, or file this under a free id."
        )
    held = f", leased by {it.lease.holder}" if it.lease else ""
    return (
        f"{item} already exists: {what} ({it.state}{held}). Adding never changes an "
        f"existing item -- use `ddflow update {item} --title ... --globs ...` to change "
        f"it, or file this under a free id."
    )


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
    from ..core.model import ABANDONED, DONE

    ports = [o.id for o in st.items.values() if o.port_from == it.id and not o.removed]
    if it.port_from or ports:
        return (
            f"{it.id} is part of a planned port ({', '.join(ports) or 'from ' + it.port_from}); "
            f"its lines were fixed when the port was planned. Abandon and re-file it with "
            f"the lines you want."
        )
    if it.worktree or it.branch or it.state in (DONE, ABANDONED) or it.lease:
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
    agent: str = "",
) -> O.Outcome:
    """Add a phase — an umbrella that completes when its tasks do.

    ``line`` puts the whole phase on a release line; its tasks inherit it. An id already
    in the queue is REFUSED; ``readd`` lets a removed one come back.
    """
    bad = _bad_id(item)
    if bad:
        return O.failed("phase.added", bad, id=item)
    log, cfg, _st = _load(repo, agent)
    bad = _bad_line(cfg, line) if line else ""
    if bad:
        return O.failed("phase.added", bad, id=item)
    with log.transaction():
        taken = _taken(_fresh(log), item, readd=readd)
        if taken:
            return O.refused("phase.added", taken, id=item)
        log.append(
            "phase.added",
            item,
            {
                "title": title,
                "needs": csv_list(needs),
                "globs": csv_list(globs),
                "body": body,
                "tags": csv_list(tags),
                "priority": priority,
                "line": line,
            },
        )
    return O.ok("phase.added", id=item)


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
    readd: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Add a task. Its parent may be a phase OR another task (making it a sub-task).

    ``line`` puts it on one release line. ``lines`` (comma-separated) files a fix that
    must reach SEVERAL: the task is written on the line `port_strategy` dictates and a
    port item is generated for each other line -- `<id>@<line>`, an ordinary task with
    its own branch, gates and merge, which starts once what it carries has landed.

    Tasks can be added at ANY time, including while their parent is being worked: a task
    that turns out to contain two things is the normal case, not an exception, and a
    queue that cannot absorb that discovery pushes the work into someone's head.

    An id already in the queue is REFUSED: adding never changes an item, `update` does.
    ``readd`` lets a REMOVED id come back with the new definition.
    """
    from ..core import flow as F
    from ..services import choices as CH
    from ..services import leases as L

    bad = _bad_id(item)
    if bad:
        return O.failed("task.added", bad, id=item)
    log, cfg, st = _load(repo, agent)
    if parent and parent not in st.items:
        return O.failed(
            "task.added", f"no such parent {parent!r}. Add the phase or task first.", id=item
        )
    wanted = csv_list(lines) or ([line] if line else [])
    for ln in wanted:
        bad = _bad_line(cfg, ln)
        if bad:
            return O.failed("task.added", bad, id=item)
    with log.transaction():
        st = _fresh(log)
        taken = _taken(st, item, readd=readd)
        if taken:
            return O.refused("task.added", taken, id=item)
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
        base = {
            "parent": parent,
            "globs": csv_list(globs),
            "body": body,
            "tags": csv_list(tags),
            "priority": priority,
        }
        log.append(
            "task.added",
            item,
            {
                **base,
                "title": title,
                "needs": csv_list(needs),
                "line": plan.author if plan else (wanted[0] if wanted else ""),
            },
        )
        ports: list[str] = []
        for ln, frm in plan.ports if plan else []:
            source = item if frm < 0 else ports[frm]
            pid = f"{item}@{ln}"
            log.append(
                "task.added",
                pid,
                {
                    **base,
                    "title": f"{title or item} (port to {ln})",
                    "needs": [source],
                    "line": ln,
                    "port_of": item,
                    "port_from": source,
                    "port_strategy": plan.strategy,
                    "tags": [*csv_list(tags), "port"],
                },
            )
            ports.append(pid)
        # Giving a task its first child turns it into an umbrella, and an umbrella is not the
        # thing being worked -- its children are. Holding its lease from here would put a live
        # claim on globs that overlap every child's, so a SECOND agent could not take one, and
        # recovery would point at a worktree where nothing more will happen. `split` already
        # released for exactly this reason; adding a sub-task by hand is the same transition
        # by a different route, and it did not.
        parent_item = st.items.get(parent) if parent else None
        released = bool(parent_item and parent_item.kind == "task" and parent_item.lease)
        if released:
            L.release(log, parent, note=f"became an umbrella when {item} was added")
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
    from ..core.model import ABANDONED, DONE
    from ..services import leases as L

    log, _cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("task.split", f"no such item {item!r}{gone}", id=item, created=[])
    if it.state in (DONE, ABANDONED):
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
        sub_id = sub_id.strip() or f"{item}.{i}"
        if sub_id in st.items:
            return O.failed(
                "task.split", f"{sub_id} already exists; choose another id", id=item, created=[]
            )
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
        log.append(
            "task.added",
            sub_id,
            {
                "parent": item,
                "title": title,
                # Inherit the parent's globs unless the child declares its own: while the
                # split is half-done the children are the only things being worked, and a
                # child with no declared globs is a child the conflict detector cannot
                # protect.
                "globs": csv_list(globs) or list(it.globs),
                "needs": csv_list(needs) if i == 1 else [],
                "priority": it.priority,
            },
        )
        created.append(sub_id)

    if it.lease:
        # The umbrella is no longer the thing being worked; holding its lease would block
        # its own children on a glob conflict with itself.
        L.release(log, item, note=f"split into {', '.join(created)}")
    log.append(
        "task.updated",
        item,
        {"body": (it.body + "\n\n" if it.body else "") + f"Split into: {', '.join(created)}."},
    )
    return O.ok("task.split", item=item, created=created, inherited_globs=not csv_list(globs))
