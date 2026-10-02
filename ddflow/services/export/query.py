"""The read side of an export: load the log once, index it once, hand kinds a Query.

Everything a document kind needs is a method here, so a kind is a pure function of
``(Query, Filters)`` and never touches the log, git or the clock itself.

The index is built in ONE pass over ``state.items``. The obvious lookup -- "tasks of this
phase" as a scan of every item per phase -- is quadratic: it took 6.6 s on a 5,632-item
log. ``Query.children`` is a dict built once, and every ordering goes through
``item_key``/``by_id`` so output is stable (explicit sort key, id tie-break) whatever order
the fold happened to produce.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from ...core.events import Event
from ...core.model import Bug, Item, State, fold

T = TypeVar("T")

#: Exit codes (the CLI maps these; the library only raises).
EXIT_UNAVAILABLE = 2  # could not run: the log or git could not be read
EXIT_REFUSED = 3  # refused: bad filter, unsafe path, hand-edited file, ...


class ExportError(Exception):
    """An export that did not happen. ``code`` is the exit code a surface should use.

    A failure to read the log or git is ``EXIT_UNAVAILABLE`` -- never an empty, clean
    looking document: a changelog that silently says "nothing happened" is worse than none.
    """

    def __init__(self, message: str, code: int = EXIT_UNAVAILABLE) -> None:
        super().__init__(message)
        self.code = code


def item_key(i: Item) -> tuple[int, str]:
    """Order for items: priority, then id. The id tie-break makes it total."""
    return (i.priority, i.id)


def by_id(x: Any) -> str:
    """Sort key for anything with an ``id`` (decisions, lessons, bugs, sessions)."""
    return str(x.id)


@dataclass
class Query:
    """A loaded log plus its indexes. Build once per export; every method is read-only."""

    state: State
    events: list[Event] = field(default_factory=list)
    #: Unparseable log lines skipped on load (a torn final line); kinds may mention it.
    skipped_lines: int = 0
    #: parent id -> live child items sorted by ``item_key``. One pass, built in __post_init__.
    children: dict[str, list[Item]] = field(init=False, default_factory=dict)
    _phases: list[Item] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        kids: dict[str, list[Item]] = {}
        phases: list[Item] = []
        for i in self.state.items.values():
            if i.removed:
                continue
            if i.kind == "phase":
                phases.append(i)
            kids.setdefault(i.parent, []).append(i)
        for v in kids.values():
            v.sort(key=item_key)
        self.children = kids
        self._phases = sorted(phases, key=item_key)

    # -- items ----------------------------------------------------------------------
    def phases(self) -> list[Item]:
        return list(self._phases)

    def item(self, item_id: str) -> Item | None:
        return self.state.items.get(item_id)

    def tasks_of(self, parent: str) -> list[Item]:
        """DIRECT task children of ``parent`` (a phase, or a task for its sub-tasks)."""
        return [c for c in self.children.get(parent, ()) if c.kind == "task"]

    def tasks_under(self, parent: str) -> list[Item]:
        """Tasks nested any depth below ``parent``, ``item_key`` order, each once."""
        out: list[Item] = []
        stack = [parent]
        seen = {parent}
        while stack:
            for c in self.children.get(stack.pop(), ()):
                if c.id in seen:
                    continue
                seen.add(c.id)
                if c.kind == "task":
                    out.append(c)
                stack.append(c.id)
        return sorted(out, key=item_key)

    def tasks(self, state: str = "") -> list[Item]:
        """Every live task (optionally in one ``state``), ``item_key`` order."""
        out = [i for i in self.state.items.values() if i.kind == "task" and not i.removed]
        if state:
            out = [i for i in out if i.state == state]
        return sorted(out, key=item_key)

    # -- the other record types, in a stable order -------------------------------------
    def bugs(self) -> list[Bug]:
        return sorted(self.state.bugs.values(), key=by_id)

    def decisions(self) -> list[Any]:
        return sorted(self.state.decisions.values(), key=by_id)

    def lessons(self) -> list[Any]:
        return sorted(self.state.lessons.values(), key=by_id)

    def sessions(self) -> list[Any]:
        return sorted(self.state.sessions.values(), key=by_id)

    # -- events ---------------------------------------------------------------------
    def events_of(self, *kinds: str, since: str = "") -> list[Event]:
        """Events (log order, which is the deterministic ``sort_key`` order) of ``kinds``.

        ``since`` is an ISO date or timestamp prefix compared against ``ts``. A document
        that needs *when* something happened for display must take it from event data,
        never from the clock at render time.
        """
        want = set(kinds)
        return [e for e in self.events if (not want or e.kind in want) and e.ts >= since]

    @property
    def last_event_id(self) -> str:
        """The id of the last event in log order ("" for an empty log)."""
        if not self.events:
            return ""
        e = self.events[-1]
        return e.id or e.compute_id()


def build(events: Iterable[Event], skipped_lines: int = 0) -> Query:
    """A Query from events already in memory (tests, and callers that hold the log)."""
    evs = list(events)
    return Query(fold(evs, strict=False), evs, skipped_lines)


def load(root: Path | str, log_cfg: Any = None) -> Query:
    """Read ``<root>/.ddflow/events`` and fold it. Raises ``ExportError`` (exit 2) if the
    log is missing or cannot be read -- never returns an empty Query for a broken log."""
    from ...infra.log import EventLog

    root = Path(root)
    if not (root / ".ddflow" / "events").is_dir():
        raise ExportError(
            f"no event log at {root / '.ddflow' / 'events'}; is this project adopted?"
        )
    log = EventLog(root, log_cfg=log_cfg)  # the caller's `cfg.log`, so [log] knobs apply
    try:
        events = log.read_all()
    except (OSError, ValueError) as exc:
        raise ExportError(f"could not read the event log: {exc}") from exc
    return build(events, log.skipped_lines)


def sorted_by(items: Iterable[T], key: Callable[[T], Any], tiebreak: Callable[[T], str]) -> list[T]:
    """Stable total order: ``key`` then ``tiebreak`` (an id). For kinds sorting anything
    the helpers above do not cover."""
    return sorted(items, key=lambda x: (key(x), tiebreak(x)))
