"""`FakeHumanChannel`: a scripted, recording `HumanChannel`. Nobody is asked, nothing is sent.

Script the person with ``answers``: a string is what they typed (an option's label or number, or
free text), an `Answer` is returned as it stands, so a test can have a channel go pending, go away
or lie. When the script runs out the person is gone and the channel is unavailable, as on the
terminal. Every call is read back from ``calls``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ..notify import (
    ANSWERED,
    APPROVAL_OPTIONS,
    DELIVERED,
    INFO,
    UNAVAILABLE,
    Answer,
    Approval,
    Delivery,
    check_severity,
    pick,
)
from . import fake


@fake
@dataclass
class FakeHumanChannel:
    """``answers``: the script. ``who``: the person. ``calls``: ``("notify", message, severity)``,
    ``("ask", question, options)`` or ``("relay", approval)`` in order."""

    name: str = "fake"
    who: str = "person"
    answers: list[str | Answer] = field(default_factory=list)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        self.calls.append(("notify", message, severity))
        return Delivery(self.name, DELIVERED)

    def _next(self, options: Sequence[str]) -> Answer:
        while self.answers:
            line = self.answers.pop(0)
            if isinstance(line, Answer):
                return line
            if picked := pick(line, options):
                return Answer(ANSWERED, picked, who=self.who, how=self.name)
        return Answer(UNAVAILABLE, how=self.name, detail="the script ran out")

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        self.calls.append(("ask", question, tuple(options)))
        return self._next(options)

    def relay(self, approval: Approval) -> Answer:
        self.calls.append(("relay", approval))
        got = self._next(APPROVAL_OPTIONS)
        if got.status == ANSWERED and not got.digest:
            return replace(got, digest=approval.digest)
        return got
