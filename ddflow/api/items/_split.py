"""Splitting an item into sub-tasks (`split`), in place, without losing its history."""

from __future__ import annotations

from pathlib import Path

from ddflow.api import items as _items

from ...config import csv_list
from ...core import globspec as GS
from ...core import ids as IDS
from ...core import outcome as O
from ...services import items as IT

#: Splitting into one piece is a rename, not a split.
MIN_SPLIT_PARTS = 2


def _plan_split(cfg, st, it, specs: list[str]) -> tuple[list[tuple[str, str]], O.Outcome | None]:
    """[(id, title)] for every part of a split, or the refusal: every child is resolved
    and validated BEFORE anything is appended."""
    item = it.id
    planned: list[tuple[str, str]] = []
    for i, spec in enumerate(specs, 1):
        sub_id, _, title = spec.partition("=")
        # the parent's own spelling: an existing id is not re-checked (render check=False)
        sub_id = sub_id.strip() or IDS.render(cfg, "split_child", check=False, parent=item, seq=i)
        if sub_id in st.items:
            return [], O.failed(
                "task.split", f"{sub_id} already exists; choose another id", id=item, created=[]
            )
        bad = IT.bad_id(sub_id)
        if bad:
            return [], O.failed("task.split", bad, id=item, created=[])
        if sub_id in [p for p, _ in planned]:
            return [], O.failed(
                "task.split",
                f"{sub_id} given twice in one split; each part needs its own id",
                id=item,
                created=[],
            )
        if sub_id == item:
            return [], O.failed(
                "task.split", f"{sub_id} cannot be its own sub-task", id=item, created=[]
            )
        planned.append((sub_id, title.strip() or f"{it.title} (part {i})"))
    return planned, None


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

    log, cfg, st = _items._load(repo, agent)
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
    planned, refusal = _plan_split(cfg, st, it, specs)
    if refusal is not None:
        return refusal

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
    IT.release_umbrella(log, st, item, note=f"split into {', '.join(created)}", tasks_only=False)
    log.append(
        "task.updated",
        item,
        {"body": (it.body + "\n\n" if it.body else "") + f"Split into: {', '.join(created)}."},
    )
    return O.ok("task.split", item=item, created=created, inherited_globs=not GS.parse(globs))
