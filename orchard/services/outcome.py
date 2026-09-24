"""What an operation did — the ONE description of a result.

Every service operation returns an `Outcome`. The machine surface is its `data`; the
human surface is **derived from that same data** by exactly one renderer in
`views.human`. Neither surface describes the result independently.

That is the whole point. Before this, each command built its JSON view and its human
view inline and separately — 20 hand-rolled `if c.json` branches and 195 bare `print`
calls across one 3,000-line module — and the project has already shipped the bug that
produces. Twice in one function, and the comment is still in the source:

    # The coverage gap must reach BOTH surfaces. It used to print only in human mode,
    # so an agent driving over MCP -- which is always JSON -- completed an item and was
    # never told that a gate had not run.

Two views of one result, hand-kept in sync, drift in the direction of whichever one the
author was looking at. Deriving one from the other makes the drift unrepresentable.

`exit` carries the vocabulary the whole package speaks, at every layer and on both
surfaces:

    0  healthy        1  real failure
    2  could not run / nothing to do        3  coordination refused

`2` is never collapsed into `0`. "No data" is reported as itself, never as "no problem"
— that rule is why `orchard next` with an empty queue and `orchard next` with a blocked
queue are distinguishable without reading the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OK = 0
FAIL = 1
NOTHING = 2
REFUSED = 3

#: Human labels, for error messages that need to name a code.
EXIT_NAMES = {OK: "ok", FAIL: "failed", NOTHING: "nothing", REFUSED: "refused"}


@dataclass
class Outcome:
    """One operation's result.

    ``kind`` selects the renderer in `views.human`; it is not free text. A kind with no
    renderer is a result that cannot be shown to a person, and `tests/test_views.py`
    refuses to let one exist — the same shape as `events.KINDS` being derived from
    `model.HANDLERS` so an event kind nothing interprets cannot exist either.
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    exit: int = OK

    #: Set when the operation refused or failed: the reason, addressed to whoever has
    #: to act on it. Kept out of `data` because every renderer shows it the same way and
    #: because a caller checking "did this work" should not have to know the kind.
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.exit == OK

    def __bool__(self) -> bool:
        return self.ok


def ok(kind: str, **data: Any) -> Outcome:
    return Outcome(kind=kind, data=data)


def nothing(kind: str, reason: str = "", **data: Any) -> Outcome:
    """Nothing to do. NOT a failure, and never rendered as success."""
    return Outcome(kind=kind, data=data, exit=NOTHING, reason=reason)


def refused(kind: str, reason: str, **data: Any) -> Outcome:
    """Coordination said no: a lease is held, a dependency is open, a gate has not run.

    Distinct from `failed` because the remedy is different — a refusal is answered by
    waiting, re-ordering, or satisfying the condition, and an agent that cannot tell the
    two apart retries the wrong one.
    """
    return Outcome(kind=kind, data=data, exit=REFUSED, reason=reason)


def failed(kind: str, reason: str, **data: Any) -> Outcome:
    return Outcome(kind=kind, data=data, exit=FAIL, reason=reason)
