"""`FakeForge`: a scripted `Forge` for tests, so a flow test never starts a real `gh`/`glab`.

It holds one `PRInfo` per number (set up front by the test), scripts the review threads and
the merge answer, and records every port call in `calls` so a test can assert what an adapter
was asked. It reaches nothing outside the process. A test scripts a canned
`ForgeUnavailable`/`ForgeError` by putting it in `script[method]`; that raise happens instead
of the call recording, exactly as the real adapter answers "could not ask" / "asked, and no"
without a "call" for the test to read.
"""

from __future__ import annotations

from ..forge import PRInfo, Thread
from . import fake


@fake
class FakeForge:
    def __init__(self, name: str = "github") -> None:
        self.name = name
        #: number -> PRInfo; `view` returns (and `find` matches on) this table.
        self.requests: dict[int, PRInfo] = {}
        #: number -> list[Thread]; `threads` returns this table (empty when absent).
        self._threads: dict[int, list[Thread]] = {}
        #: method name -> one canned raise, raised instead of the call recording.
        self.script: dict[str, Exception] = {}
        #: The string `merge` returns (the forge's answer), scripted by the test.
        self.merge_answer: str = "merged"
        self.calls: list[tuple] = []

    def _answer(self, method: str, *args: object) -> None:
        if method in self.script:
            raise self.script[method]
        self.calls.append((method, *args))

    def _info(self, number: int) -> PRInfo:
        """The stored `PRInfo` for `number`, fabricated once (and stored) when absent."""
        if number not in self.requests:
            self.requests[number] = PRInfo(number=number)
        return self.requests[number]

    def find(self, head: str) -> PRInfo | None:
        self._answer("find", head)
        for info in self.requests.values():
            if info.head == head:
                return info
        return None

    def create(self, *, head, base, title, body, draft, labels, reviewers) -> PRInfo:
        self._answer("create", head, base, title, body, draft, labels, reviewers)
        number = max(self.requests, default=0) + 1
        info = PRInfo(number=number, head=head, base=base, draft=draft)
        self.requests[number] = info
        return info

    def view(self, number: int) -> PRInfo:
        self._answer("view", number)
        return self._info(number)

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        self._answer("merge", number, strategy, head_sha, auto)
        return self.merge_answer

    def set_base(self, number: int, base: str) -> None:
        self._answer("set_base", number, base)
        self._info(number).base = base

    def threads(self, number: int) -> list[Thread]:
        self._answer("threads", number)
        return self._threads.get(number, [])

    def reply(self, number: int, thread_id: str, body: str) -> None:
        self._answer("reply", number, thread_id, body)

    def resolve(self, number: int, thread_id: str) -> None:
        self._answer("resolve", number, thread_id)
