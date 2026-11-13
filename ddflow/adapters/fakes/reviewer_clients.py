"""`FakeReviewerClient`: a scripted `ReviewerClient` for tests, with no socket and no urllib.

It scripts one ``(content, error)`` answer and records every completion it serves in
`calls`, so a test asserts what the caller asked for. It reaches nothing outside the
process. The repository ALSO ships real adapter tests (tests/test_adapters_reviewer_clients.py)
that observe the wire body through a recording `post_json`; this fake scripts the OUTCOME,
not the wire.
"""

from __future__ import annotations

from ..reviewer_clients import ReviewerChat, ReviewerClient
from . import fake


@fake
class FakeReviewerClient(ReviewerClient):
    def __init__(self, content: str = "", error: str = "") -> None:
        self.name = "fake"
        self.key_env = ""
        self._content = content
        self._error = error
        #: (chat,) per served completion, in call order, for a test to assert on.
        self.calls: list[ReviewerChat] = []

    def complete(self, chat: ReviewerChat) -> tuple[str, str]:
        self.calls.append(chat)
        return self._content, self._error
