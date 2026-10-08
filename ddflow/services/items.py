"""One way to create a task: ``add_task`` (B-uni-item-create).

Tasks were appended as ``task.added`` from eight places -- ``task add``, ``split``, a bug's
fix task, a trigger fire, a promotion and the importer's tasks and branches -- and each
re-implemented some of the checks the others had: the glob and id validators, the parent
check, the "id already in the queue" refusal, and the transition that turns a parent task
into an umbrella (its lease is released). This module is the one place that does all of
them, then appends the event.

``TaskDraft`` holds the fields; a field left ``None`` is not written, so each caller's
event keeps the shape it always had (the fold fills in the defaults). ``extra`` carries
what only one caller has (``fixes``, ``promote_to``, ``source`` ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core import globspec as GS
from ..core.model import Item
from . import leases as L

if TYPE_CHECKING:
    from ..config import Config
    from ..core.model import State
    from ..infra.log import EventLog

#: ``TaskDraft`` fields written to the event, in the order they are written.
_FIELDS = ("parent", "title", "needs", "globs", "resources", "body", "tags", "priority", "line")


def bad_id(item: str) -> str:
    """Why ``item`` cannot be a local id, or "". A colon is how a dependency names an item
    in ANOTHER repository (`repo:ID`); a local `foo:bar` was taken for one, looked up
    in the sibling observations, and blocked everything that needed it (rubber-duck)."""
    if ":" in item:
        return (
            f"{item!r}: a colon marks a dependency in another repository (`repo:ID`, "
            f"[schedule] repos), so it cannot be part of a local id"
        )
    return ""


def taken(st: State, item: str, *, readd: bool) -> str:
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


@dataclass
class TaskDraft:
    """A task about to be added. ``None`` = not written (the fold's default applies)."""

    id: str
    title: str | None = None
    parent: str | None = None
    needs: list[str] | None = None
    globs: list[str] | None = None
    resources: list[str] | None = None
    body: str | None = None
    tags: list[str] | None = None
    priority: int | None = None
    line: str | None = None
    #: Fields only some callers write: `fixes`, `promote_from`, `port_of`, `source` ...
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_data(cls, item: str, data: dict[str, Any]) -> TaskDraft:
        """The draft of an already-built ``task.added`` payload (a trigger fire's item)."""
        known = {k: data[k] for k in _FIELDS if k in data}
        return cls(item, extra={k: v for k, v in data.items() if k not in _FIELDS}, **known)

    def data(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in _FIELDS if getattr(self, k) is not None}
        return {**out, **self.extra}


@dataclass
class Added:
    """What ``add_task`` did. ``problem`` set: nothing was written; ``refused`` says the
    caller should report a coordination refusal (exit 3) rather than a failure."""

    id: str
    problem: str = ""
    refused: bool = False

    @property
    def ok(self) -> bool:
        return not self.problem


def problem_of(draft: TaskDraft) -> str:
    """Why the draft's id or globs cannot be recorded, or "" (needs no state)."""
    return bad_id(draft.id) or GS.problem(draft.globs or [])


def missing_parent(st: State, parent: str | None) -> str:
    """Why ``parent`` cannot hold a new task, or ""."""
    if parent and parent not in st.items:
        return f"no such parent {parent!r}. Add the phase or task first."
    return ""


def release_umbrella(log: EventLog, st: State, parent: str, *, note: str) -> bool:
    """Release ``parent``'s lease -- True when it held one.

    Giving a task its first child turns it into an umbrella, and an umbrella is not the
    thing being worked -- its children are. Holding its lease would put a live claim on
    globs that overlap every child's, so a SECOND agent could not take one, and recovery
    would point at a worktree where nothing more will happen.
    """
    holder = st.items.get(parent) if parent else None
    if not (holder and holder.lease):
        return False
    L.release(log, parent, note=note)
    holder.lease = None  # a second child added in this batch finds nothing to release
    return True


def add_task(
    log: EventLog,
    st: State,
    cfg: Config,
    draft: TaskDraft,
    *,
    dedupe: Any,
    readd: bool = False,
) -> Added:
    """Validate ``draft`` and append its ``task.added``; the caller holds the log lock and
    decided ``st`` under it.

    Validated here, for every route: the id (no colon), the globs (no quote or unbalanced
    bracket), an existing parent, and an id not already in the queue (``readd`` lets a
    REMOVED one come back). ``dedupe`` is required, so no route files a task without
    saying how it was screened (tests/test_coherence_coverage.py follows the calls): either
    the settled duplicate check (`api._dedupe.Checked`), whose fields join the event, or the
    REASON a generated task is not checked (a string). The caller writes the check's follow-up
    (`DD.after_add`) and releases a parent that became an umbrella (`release_umbrella`),
    in the order its events have always had. ``st`` gains the new item so a caller adding
    several sees the earlier ones.
    """
    if not hasattr(dedupe, "fields") and not (isinstance(dedupe, str) and dedupe.strip()):
        raise ValueError("add_task: dedupe is a settled check, or the reason there is none")
    bad = problem_of(draft) or missing_parent(st, draft.parent)
    if bad:
        return Added(draft.id, bad)
    held = taken(st, draft.id, readd=readd)
    if held:
        return Added(draft.id, held, refused=True)
    data = draft.data()
    if not isinstance(dedupe, str):
        data.update(dedupe.fields)
    log.append("task.added", draft.id, data)
    st.items[draft.id] = Item(
        id=draft.id,
        kind="task",
        title=draft.title or "",
        parent=draft.parent or "",
        fixes=list(draft.extra.get("fixes", [])),
    )
    return Added(draft.id)
