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

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    #: The project root this Query was loaded from (``load`` sets it; ``build`` leaves it
    #: ``None``). The one thing a kind may take from outside the log: the configured
    #: workflow, which lives in config and not in events.
    repo: Path | None = None
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

        ``since`` is an ISO date (a calendar day, compared on the ``ts`` text) or a full
        timestamp (compared as an instant); anything else is refused. Events whose ``ts`` is
        not date-shaped (date form) or not parseable (timestamp form) are left out. A document
        that needs *when* something happened for display must take it from event data,
        never from the clock at render time.
        """
        want = set(kinds)
        cut = _cutoff(since)
        return [
            e for e in self.events if (not want or e.kind in want) and (cut is None or cut(e.ts))
        ]

    @property
    def last_event_id(self) -> str:
        """The id of the last event in log order ("" for an empty log)."""
        if not self.events:
            return ""
        e = self.events[-1]
        return e.id or e.compute_id()


def _cutoff(since: str) -> Callable[[str], bool] | None:
    """A predicate "this ``ts`` is at or after ``since``", or None for no cutoff.

    A bare date (``2026-10-01``) compares by calendar day on the ``ts`` text; a full
    timestamp is parsed on both sides (``Z`` and ``+00:00`` are the same instant, offsets
    are honoured) -- a raw string compare puts ``...12:00:00+00:00`` before ``...12:00:00Z``.
    An unparseable ``since`` is refused, never ignored.
    """
    if not since:
        return None
    if len(since) <= _DATE_LEN:
        if not _DATE_PREFIX.match(since):
            raise ExportError(f"--since {since!r} is not an ISO date or timestamp", EXIT_REFUSED)
        try:  # a real calendar value: not 2024-13-99
            datetime.fromisoformat((since + "-01-01")[:10] if len(since) < _DATE_LEN else since)
        except ValueError:
            raise ExportError(f"--since {since!r} is not a real date", EXIT_REFUSED) from None

        def on_or_after(ts: str) -> bool:
            # An event whose ts is not a date at all cannot be shown to be in range.
            return bool(_DAY.match(ts)) and ts[: len(since)] >= since

        return on_or_after
    try:
        floor = _parse_ts(since)
    except ValueError:
        raise ExportError(
            f"--since {since!r} is not an ISO date or timestamp", EXIT_REFUSED
        ) from None

    def at_or_after(ts: str) -> bool:
        try:
            return _parse_ts(ts) >= floor
        except ValueError:
            return False  # an event with no readable time cannot be shown to be in range

    return at_or_after


_DATE_LEN = len("2026-10-01")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DATE_PREFIX = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _parse_ts(text: str) -> datetime:
    t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


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
    log = EventLog(
        root, log_cfg=log_cfg, cache_writes=False
    )  # the caller's `cfg.log`, so [log] knobs apply
    try:
        events = log.read_all()
    except (OSError, ValueError) as exc:
        raise ExportError(f"could not read the event log: {exc}") from exc
    if not events and log.skipped_lines:
        # Every line was unreadable: an empty document here would claim "nothing happened".
        raise ExportError(f"the event log has no readable events ({log.skipped_lines} bad lines)")
    q = build(events, log.skipped_lines)
    q.repo = root
    return q


def sorted_by(items: Iterable[T], key: Callable[[T], Any], tiebreak: Callable[[T], str]) -> list[T]:
    """Stable total order: ``key`` then ``tiebreak`` (an id). For kinds sorting anything
    the helpers above do not cover."""
    return sorted(items, key=lambda x: (key(x), tiebreak(x)))
