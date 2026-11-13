"""The `ReviewerClient` port: the same cases against the fake and a real adapter.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py): per
port, how to build the real adapter (a `ReviewerClient`), and the cases both must pass.
The repository's wire-body tests (tests/test_adapters_reviewer_clients.py) observe each
adapter's request shape through a recording `post_json`; this file pins only the PORT
contract.
"""

from __future__ import annotations

from ddflow.adapters.reviewer_clients import (
    OpenAICompatibleClient,
    ReviewerChat,
)


class _RecordingPost:
    """A scripted, recording `post_json` that returns a scripted body with no network."""

    def __init__(self, body: dict | None = None):
        self._body = body
        self.calls: list[tuple] = []

    def __call__(self, url, payload, headers, timeout_s):
        self.calls.append((url, payload, headers, timeout_s))
        return self._body, ""


def _cases(client) -> None:
    """The port contract: `complete` returns ``(content, error)`` and never raises."""
    post = _RecordingPost({"choices": [{"message": {"content": "ok"}}]})
    chat = ReviewerChat(system="s", user="u", timeout_s=5, post_json=post)
    content, error = client.complete(chat)
    assert isinstance(content, str)
    assert isinstance(error, str)


def _real(monkeypatch) -> OpenAICompatibleClient:
    return OpenAICompatibleClient()


CONFORMANCE = {
    "ReviewerClient": {
        "real": _real,
        "cases": _cases,
    },
}
