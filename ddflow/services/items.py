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

import difflib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ..core import globspec as GS
from ..core import schedule as SCH
from ..core.defaults import DEFAULT_PRIORITY
from ..core.model import DONE, WORK_KINDS, Item
from . import leases as L
from . import priority as P
from . import wiring as W

if TYPE_CHECKING:
    from ..config import Config
    from ..core.model import State
    from ..infra.log import EventLog

#: ``TaskDraft`` fields written to the event, in the order they are written.
_FIELDS = (
    "parent",
    "title",
    "needs",
    "globs",
    "resources",
    "body",
    "tags",
    "priority",
    "line",
    "kind",
    "adds",
)


def bad_id(item: str) -> str:
    """Why ``item`` cannot be a local id, or "". A colon is how a dependency names an item
    in ANOTHER repository (`repo:ID`); a local `foo:bar` was taken for one, looked up
    in the sibling observations, and blocked everything that needed it (rubber-duck). A
    leading hyphen is refused too: every command that then names the item (``show``,
    ``claim``, ``update``, ...) parses the id as an option, which no quoting can fix."""
    if item.startswith("-"):
        return (
            f"{item!r}: an item id may not start with a hyphen -- every command that "
            f"names it then parses the id as an option, which quoting cannot fix"
        )
    if ":" in item:
        return (
            f"{item!r}: a colon marks a dependency in another repository (`repo:ID`, "
            f"[schedule] repos), so it cannot be part of a local id"
        )
    return ""


def bad_title(title: str | None) -> str:
    """Why ``title`` cannot be an item's title, or "".

    An empty (or blank) title leaves board, brief and gate rows showing only the id,
    which says nothing about the work (bug Bbd67278144). Refusing it at creation keeps
    the queue legible; `ddflow update <id> --title` is the remedy for an item already
    filed without one (``doctor`` names those).
    """
    if not title or not title.strip():
        return (
            "the item needs a title: without one every board, brief and gate row shows "
            "only the id. Give one now (`--title ...`), or `ddflow update <id> "
            "--title ...` for an item already in the queue."
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
    #: A work kind other than a plain task ("doc", ...); None writes nothing: a task.
    kind: str | None = None
    #: The feature classes this item introduces (`knob`, `event`, `command`), whose wiring
    #: files join its globs (`services.wiring`). None writes nothing: a task that adds none.
    adds: list[str] | None = None
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
    """Why the draft's id, globs or kind cannot be recorded, or "" (needs no state)."""
    return (
        bad_id(draft.id)
        or GS.problem(draft.globs or [])
        or SCH.problem(draft.resources or [])
        or _bad_kind(draft.kind)
        or _bad_adds(draft.adds)
    )


def problem_of_add(draft: TaskDraft, title: str) -> str:
    """`problem_of` plus the title an item made with `task add`/`phase add` must name.

    The title is validated only here, NOT in `problem_of`: `add_task` still files a
    title-less draft on import and other routes, and their fold defaults, not a refused
    title, are their contract (bug Bbd67278144 makes the USER-FACING add require one).
    """
    return problem_of(draft) or bad_title(title)


def _bad_kind(kind: str | None) -> str:
    """Why ``kind`` is not an item kind `task add` can make, or "" (none, or a work kind)."""
    if not kind or kind in WORK_KINDS:
        return ""
    if kind == "phase":
        return "a phase is made with `ddflow phase add`, not `task add --kind phase`."
    return f"unknown kind {kind!r}: task add makes {', '.join(WORK_KINDS)}."


def _bad_adds(adds: list[str] | None) -> str:
    """Why ``adds`` cannot name what this item introduces, or "" (none, or a feature class)."""
    return W.problem(adds)


def missing_parent(st: State, parent: str | None) -> str:
    """Why ``parent`` cannot hold a new task, or ""."""
    if parent and parent not in st.items:
        return f"no such parent {parent!r}. Add the phase or task first."
    return ""


def closed_phase_parent(
    st: State, parent: str | None, *, readd: bool = False, item: str = ""
) -> str:
    """Why a NEW open task under ``parent`` is REFUSED, or "" (bug B86e99de0c6): a
    truly-closed phase -- one the importer wrote (or `phase close`d), which reports done
    with ``imported`` authority -- cannot take an open task, or the task would sit beneath
    a closed phase. The operator re-opens it first (`ddflow verify --reopen <phase>`); a
    phase completed normally re-opens on its own when it gains an open task (see
    ``handlers.items._reopen_done_parent``).

    A ``readd`` that restores a still-settled task is NOT refused: it adds no open work
    under the phase, so it may land beneath a closed phase without violating the invariant.
    """
    if not parent:
        return ""
    holder = st.items.get(parent)
    if holder is None or holder.kind != "phase" or holder.state != DONE:
        return ""
    if holder.id not in st.imported_done:
        return ""
    if readd and (existing := st.items.get(item)) is not None and existing.terminal:
        return ""
    return (
        f"phase {parent!r} is closed (imported): re-open it before filing a task under "
        f"it (`ddflow verify --reopen {parent}`)"
    )


def unknown_needs(st: State, needs: list[str] | None) -> str:
    """Why any of ``needs`` names no id in the queue -- a typo that would wait on an id
    that never exists -- with a 'did you mean', or "". Checked on `task add` and on
    `update --needs`/`--add-needs`, where an unknown id was accepted silently and the
    dependent then waited forever (B4d23497469)."""
    for need in needs or []:
        if need not in st.items:
            near = difflib.get_close_matches(need, sorted(st.items), n=1, cutoff=0.6)
            hint = f" Did you mean '{near[0]}'?" if near else ""
            return f"no such id {need!r}. Add it first, or fix the typo.{hint}"
    return ""


def release_umbrella(
    log: EventLog, st: State, parent: str, *, note: str, tasks_only: bool = True
) -> bool:
    """Release ``parent``'s lease -- True when it held one.

    Giving a task its first child turns it into an umbrella, and an umbrella is not the
    thing being worked -- its children are. Holding its lease would put a live claim on
    globs that overlap every child's, so a SECOND agent could not take one, and recovery
    would point at a worktree where nothing more will happen. A claimed PHASE keeps its
    claim when a task is added under it (a phase with tasks is its normal state) unless
    ``tasks_only`` is False: `split` lets any item go.
    """
    holder = st.items.get(parent) if parent else None
    if not (holder and holder.lease) or (tasks_only and not holder.is_work):
        return False
    L.release(log, parent, note=note)
    holder.lease = None  # a second child added in this batch finds nothing to release
    return True


def _validation_refusal(st: State, draft: TaskDraft, readd: bool) -> Added | None:
    """The ``got refused / failed`` for a draft that cannot be filed, or None when the id,
    globs, kind, or parent are all acceptable. Broken out of ``add_task`` so the closed-
    phase refusal and the shape checks live beside it here, in one loop count."""
    closed = closed_phase_parent(st, draft.parent, readd=readd, item=draft.id)
    if closed:
        return Added(draft.id, closed, refused=True)
    bad = problem_of(draft) or missing_parent(st, draft.parent)
    if bad:
        return Added(draft.id, bad)
    return None


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
    refused = _validation_refusal(st, draft, readd)
    if refused is not None:
        return refused
    held = taken(st, draft.id, readd=readd)
    if held:
        return Added(draft.id, held, refused=True)
    if draft.priority is None:  # filed with none: a DX or fix task takes its band
        banded = P.band(cfg, draft.id, draft.tags or [])
        if banded != DEFAULT_PRIORITY:
            draft = replace(draft, priority=banded)
    # A feature item declares what it adds (`adds`): its globs grow the wiring files
    # that introduction touches, so the lease covers them before the first write
    # (`services.wiring`, B-item-globs-omit-wiring-files).
    if draft.adds:
        draft = replace(draft, globs=W.expand(draft.globs, draft.adds))
    data = draft.data()
    if not isinstance(dedupe, str):
        data.update(dedupe.fields)
    log.append("task.added", draft.id, data)
    st.items[draft.id] = Item(
        id=draft.id,
        kind="task",
        title=draft.title or "",
        parent=draft.parent or "",
        needs=list(draft.needs or []),
        globs=list(draft.globs or []),
        resources=list(draft.resources or []),
        body=draft.body or "",
        tags=list(draft.tags or []),
        priority=DEFAULT_PRIORITY if draft.priority is None else draft.priority,
        line=draft.line or "",
        fixes=list(draft.extra.get("fixes", [])),
    )
    return Added(draft.id)
