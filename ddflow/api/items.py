"""Creating and changing queue items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ._base import _load


def update(
    repo: Path,
    item: str,
    *,
    agent: str = "",
    title: str | None = None,
    body: str | None = None,
    needs: list[str] | None = None,
    globs: list[str] | None = None,
    tags: list[str] | None = None,
    priority: int | None = None,
) -> O.Outcome:
    """Change an item's fields. `None` means "leave alone"; `[]` means "clear".

    That sentence is the whole reason this function exists. Over argv the two collapse
    into one empty string, and the surface had to carry a `clearable` flag to tell them
    apart — a distinction the type system expresses for free.
    """
    log, _cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("item.updated", f"no such item {item!r}{gone}", id=item)

    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if needs is not None:
        fields["needs"] = list(needs)
    if globs is not None:
        fields["globs"] = list(globs)
    if tags is not None:
        fields["tags"] = list(tags)
    if priority is not None:
        fields["priority"] = int(priority)

    if not fields:
        return O.nothing(
            "item.updated",
            "nothing to change: every field was left unset. Pass a value to set one, "
            "or an empty list to clear it.",
            id=item,
        )
    log.append(f"{it.kind}.updated", item, fields)
    return O.ok("item.updated", id=item, changed=sorted(fields), fields=fields)


#: Splitting into one piece is a rename, not a split.
MIN_SPLIT_PARTS = 2

#: Where an item sits when nobody says otherwise. The MIDDLE of the range, so a later
#: item can be pushed either way without renumbering anything.
#:
#: Declared here and imported by the parser, not written twice. Duplicated, it became
#: 100 in argparse and 0 in this layer — so every phase and task created over MCP was
#: filed at the TOP priority while the CLI filed them in the middle, and nothing said so.
DEFAULT_PRIORITY = 100


def phase_add(
    repo: Path,
    item: str,
    *,
    title: str = "",
    needs: str = "",
    globs: str = "",
    body: str = "",
    tags: str = "",
    priority: int = DEFAULT_PRIORITY,
    agent: str = "",
) -> O.Outcome:
    """Add a phase — an umbrella that completes when its tasks do."""
    log, _cfg, _st = _load(repo, agent)
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
        },
    )
    return O.ok("phase.added", id=item)


def task_add(
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
    agent: str = "",
) -> O.Outcome:
    """Add a task. Its parent may be a phase OR another task (making it a sub-task).

    Tasks can be added at ANY time, including while their parent is being worked: a task
    that turns out to contain two things is the normal case, not an exception, and a
    queue that cannot absorb that discovery pushes the work into someone's head.
    """
    from ..services import leases as L

    log, _cfg, st = _load(repo, agent)
    if parent and parent not in st.items:
        return O.failed(
            "task.added", f"no such parent {parent!r}. Add the phase or task first.", id=item
        )
    log.append(
        "task.added",
        item,
        {
            "parent": parent,
            "title": title,
            "needs": csv_list(needs),
            "globs": csv_list(globs),
            "body": body,
            "tags": csv_list(tags),
            "priority": priority,
        },
    )
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
    return O.ok("task.added", id=item, parent=parent, released_parent_lease=released)


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
