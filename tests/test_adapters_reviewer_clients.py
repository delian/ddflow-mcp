"""The `ReviewerClient` adapters: one wire protocol per adapter, behind one port.

Conformance (ADP-reviewer-clients): the three kinds are registered by ``kind``, each shapes
its own request and auth header, classifies its own errors and detects its own truncation,
over a recording ``post_json`` fake -- so a request is observed as the endpoint would see
it, with no socket and no urllib. The port is structural (`ReviewerClient`), the registry
is `CLIENTS`, and the wire bodies live in the adapter, not the review service.
"""

from __future__ import annotations

import pytest

from ddflow.adapters.reviewer_clients import (
    CLIENTS,
    AnthropicClient,
    GeminiClient,
    OpenAICompatibleClient,
    ReviewerChat,
    temperature_field,
    truncated_reason,
)

STATUS = "STATUS: NO FINDINGS"


class RecordingPost:
    """A scripted, recording `post_json`: records each ``(url, payload, headers, timeout)``
    and returns a scripted ``(body, error)``. The adapter layer calls this INSTEAD of the
    HTTP client, so its shaping is observed without a network."""

    def __init__(self, body=None, err=""):
        self._body = body
        self._err = err
        self.calls: list[tuple] = []

    def __call__(self, url, payload, headers, timeout_s):
        self.calls.append((url, payload, headers, timeout_s))
        return self._body, self._err


def _ok(text: str) -> bool:
    return "STATUS:" in text


def _chat(
    client, body, *, err="", scrub=None, key="", **kw
) -> tuple[RecordingPost, tuple[str, str]]:
    post = RecordingPost(body, err)
    chat = ReviewerChat(
        system="system text",
        user="user text",
        timeout_s=5,
        model="m-1",
        base_url="https://ep.invalid/v1",
        max_tokens=64,
        has_verdict=_ok,
        scrub=scrub or (lambda t: t),
        post_json=post,
        **kw,
    )
    return post, client.complete(chat)


def test_the_three_wire_kinds_are_registered_by_kind():
    assert CLIENTS.names() == ["anthropic", "gemini", "openai"]
    assert {c.name for c in CLIENTS.values_list()} == {"openai", "anthropic", "gemini"}


def test_openai_posts_a_bearer_authorization_header():
    post, (text, err) = _chat(
        OpenAICompatibleClient(),
        {"choices": [{"message": {"content": STATUS}}]},
        api_key="k-1",
    )
    assert err == "" and text == STATUS
    url, payload, headers, _ = post.calls[0]
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer k-1"
    assert payload["model"] == "m-1" and payload["max_tokens"] == 64


def test_anthropic_uses_x_api_key_and_a_top_level_system():
    post, (text, err) = _chat(
        AnthropicClient(),
        {"content": [{"type": "text", "text": STATUS}], "stop_reason": "end_turn"},
        api_key="k-2",
    )
    assert err == "" and text == STATUS
    url, payload, headers, _ = post.calls[0]
    assert url.endswith("/messages")
    assert headers["x-api-key"] == "k-2"
    assert payload["system"] == "system text"
    assert payload["messages"] == [{"role": "user", "content": "user text"}]


def test_gemini_puts_the_key_in_a_header_never_the_url():
    post, (text, err) = _chat(
        GeminiClient(),
        {"candidates": [{"content": {"parts": [{"text": STATUS}]}, "finishReason": "STOP"}]},
        api_key="k-3",
    )
    assert err == "" and text == STATUS
    url, payload, headers, _ = post.calls[0]
    assert headers["x-goog-api-key"] == "k-3"
    assert "k-3" not in url and "key=" not in url
    assert payload["generationConfig"]["maxOutputTokens"] == 64


def test_a_keyless_endpoint_sends_no_auth_header():
    post, (text, err) = _chat(
        OpenAICompatibleClient(),
        {"choices": [{"message": {"content": STATUS}}]},
    )
    assert err == "" and text == STATUS
    assert "Authorization" not in post.calls[0][2]


def test_an_http_status_error_is_scrubbed_of_the_key():
    _post, (text, err) = _chat(
        OpenAICompatibleClient(),
        {"choices": []},
        err="HTTP 401: bad key sk-live-ABC",
        api_key="sk-live-ABC",
        scrub=lambda t: t.replace("sk-live-ABC", "[REDACTED]"),
    )
    assert text == "" and err.startswith("HTTP 401")
    assert "sk-live-ABC" not in err


@pytest.mark.parametrize(
    "client,body",
    [
        (
            OpenAICompatibleClient(),
            {"choices": [{"message": {"content": "thinking"}, "finish_reason": "length"}]},
        ),
        (
            AnthropicClient(),
            {"content": [{"type": "text", "text": "thinking"}], "stop_reason": "max_tokens"},
        ),
        (
            GeminiClient(),
            {
                "candidates": [
                    {"content": {"parts": [{"text": "thinking"}]}, "finishReason": "MAX_TOKENS"}
                ]
            },
        ),
    ],
)
def test_a_reply_at_the_token_cap_with_no_verdict_is_truncated(client, body):
    _post, (text, err) = _chat(client, body, api_key="k")
    assert text == "" and err.startswith("TRUNCATED")


def test_a_reply_at_the_token_cap_that_carries_a_verdict_is_a_review():
    body = {
        "choices": [
            {
                "message": {"content": "FINDING LOW a.py:1\nnit\n\nSTATUS: FINDINGS 1"},
                "finish_reason": "length",
            }
        ]
    }
    _post, (text, err) = _chat(OpenAICompatibleClient(), body, api_key="k")
    assert err == "" and text.startswith("FINDING LOW")


def test_temperature_is_absent_unless_set():
    assert temperature_field(None) == {}
    assert temperature_field(0.7) == {"temperature": 0.7}


def test_truncated_reason_names_both_remedies():
    msg = truncated_reason(64, None)
    assert "temperature" in msg and "max_tokens" in msg and msg.startswith("TRUNCATED")
