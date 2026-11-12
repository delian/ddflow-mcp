"""The `ReviewerClient` port: one adapter per wire protocol.

Home of the reviewer-client family. The `openai`, `anthropic` and `gemini` reviewer kinds
are one request/response shape, one auth header and one error/truncation story each -- the
port is `ReviewerClient`, declared below next to its adapters, each registered in `CLIENTS`
under its `name`. The request/response shaping, auth header, error classification (quota, a
403 leaked key, a 5xx, a timeout) and truncation detection live HERE, behind that port,
over the `HttpClient` port (`ddflow.adapters.http`); a Mistral or any new kind is then one
adapter in this registry, never another `elif` in the review service.

The adapter layer may not import `ddflow.services` (D-adapters-answers), so the pieces a
completion still needs from the review service -- the HTTP plumbing that applies the
container's `127.0.0.1` rewrite and attaches a hedged copy's socket, the key scrubber, and
the "does this text carry a verdict" test -- are handed to `complete` in a `ReviewerChat`
the caller builds. The wire-level decisions (payload shape, auth header, how a reply is
parsed and when it is ``TRUNCATED``) are the adapter's own.

The ``kind='command'`` reviewer is NOT a wire protocol -- a prompt on stdin, a reply on
stdout -- and stays where the review scheduler's process/cancellation machinery lives; it is
deliberately not a `ReviewerClient`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .http import HttpIncompleteRead
from .ports import port
from .registry import Registry

#: Marks a request the endpoint ACCEPTED but did not answer within its timeout.
DID_NOT_CONVERGE = "DID NOT CONVERGE:"

#: Marks a `truncated_reason` reply, so a chunk lost to it can be told apart and retried.
TRUNCATED = "TRUNCATED:"

#: Below this a reasoning model is sampled colder than vendors recommend (DeepSeek and
#: Qwen thinking modes: 0.6-1.0), which is where looping-to-the-budget was measured.
_COLD = 0.6


@dataclass
class ReviewerChat:
    """One completion, the way a wire-protocol adapter needs it.

    The plain fields are taken straight off a configured reviewer. The callables are the
    review-service glue the adapter layer may not import: ``post_json`` posts the JSON
    payload through the current `HttpClient` (container rewrite + hedged-socket attach
    included) and returns ``(body, error)``, ``scrub`` removes the key a server echoed, and
    ``has_verdict`` says whether a parsed reply carried a ``STATUS:`` block. ``api_key`` is
    the key it filled in already (empty when keyless); ``no_key_reason`` is why there is none.
    """

    system: str
    user: str
    timeout_s: float
    model: str = ""
    base_url: str = ""
    max_tokens: int = 32000
    temperature: float | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    api_key: str = ""
    no_key_reason: str = ""
    #: ``(url, payload, headers, timeout_s) -> (body | None, error)`` -- the review service's
    #: JSON POST, which edits the URL for the container and attaches the hedged socket.
    post_json: Callable[..., Any] = lambda url, payload, headers, timeout_s: (None, "")
    #: ``(text) -> bool`` -- ``True`` when ``text`` parses to an on-contract verdict.
    has_verdict: Callable[[str], bool] = lambda text: False
    #: ``(text) -> str`` -- ``text`` with this reviewer's key redacted, wherever a server
    #: echoed it.
    scrub: Callable[[str], str] = lambda text: text


def temperature_field(temperature: float | None) -> dict[str, float]:
    """The request's temperature field: absent unless the reviewer entry sets one."""
    return {} if temperature is None else {"temperature": temperature}


def no_answer_in_time(timeout_s: float) -> str:
    """A read timeout, told apart from an endpoint that is not there.

    `urlopen` wraps a CONNECT timeout in `URLError` -- nothing is listening, which is
    "unreachable". A timeout raised bare came after the request was sent and accepted:
    the server was still generating. Reported as "unreachable: timed out" it sent
    operators to check an endpoint that was fine, while the chunk was deterministically
    running to `max_tokens` (bug B338a8bb598: 32 000 tokens at under 36 tok/s is more
    than the 900 s timeout).
    """
    return (
        f"{DID_NOT_CONVERGE} the endpoint accepted the request but no answer came within "
        f"{timeout_s:.0f}s -- the model was still generating, most likely toward "
        f"max_tokens. Raise [[reviewer]].timeout_s, lower max_tokens, or lower "
        f"max_chunk_chars so each chunk needs less thinking. The endpoint is reachable."
    )


def cut_off(exc: Exception) -> str:
    """An `HttpProtocolError` (urllib's `http.client.HTTPException`): most often a response that
    stopped mid-read (`IncompleteRead`). Never a `TRUNCATED`, so never retried as one.

    The everyday cause is hedging: the winning copy shuts a loser's socket while the
    loser is reading its answer. Uncaught, it escaped the backend and crashed the whole
    review, whose contract is never to raise (bug Bf948d29d37).
    """
    if isinstance(exc, HttpIncompleteRead):
        return f"connection cut off mid-response: {exc}"
    # The rest of the family is the endpoint, not the reply: an InvalidURL from a bad
    # base_url port, a BadStatusLine from a proxy that does not speak HTTP (critic).
    return f"bad HTTP exchange with the endpoint: {exc}"


def truncated_reason(
    max_tokens: int, temperature: float | None, reasoning_tokens: object = None
) -> str:
    """The one way this package explains a reply that ran out of budget mid-thought.

    Written three times before -- once per backend -- and already drifted: the OpenAI
    copy named `max_chunk_chars` and reported reasoning-token usage, the Gemini copy
    named neither. A reviewer that hit this returns UNAVAILABLE, and the difference
    between "raise max_tokens" and "lower max_chunk_chars" is the difference between
    a fix and another hour of the same failure, so the remedy has to be complete
    wherever it is read.

    Measured, not guessed: on Qwen3.8-Flash-Next-FP8 over a 30 KB diff, a 6,000-token
    budget produced ZERO characters of content -- the whole budget went to reasoning.
    That is why the default is 32,000.
    """
    detail = ""
    if reasoning_tokens not in (None, "", 0):
        detail = f" ({reasoning_tokens} of them reasoning)"
    cold = (
        f"[[reviewer]].temperature = {temperature} is below what most reasoning "
        f"models are tuned for, and a model sampled cold can loop until the budget is "
        f"gone: remove it (the model's own default applies) or set the vendor's "
        f"recommended value. Otherwise raise "
        if temperature is not None and temperature < _COLD
        else "If the model's vendor recommends a temperature, check the server applies "
        "it ([[reviewer]].temperature sets one explicitly). Otherwise raise "
    )
    return (
        f"{TRUNCATED} the model consumed all {max_tokens} tokens{detail} before "
        f"emitting any answer. This is UNAVAILABLE, not a clean review. {cold}"
        f"[[reviewer]].max_tokens, or lower [[reviewer]].max_chunk_chars so each "
        f"chunk needs less thinking."
    )


@port
class ReviewerClient(Protocol):
    """One completion to a model endpoint, shaped for its wire protocol. The adapters are
    `OpenAICompatibleClient`, `AnthropicClient` and `GeminiClient`, registered by `name` in
    `CLIENTS`; the fake for tests is `FakeReviewerClient` in `ddflow.adapters.fakes`."""

    name: str
    key_env: str

    def complete(self, chat: ReviewerChat) -> tuple[str, str]:
        """One completion: returns ``(content, error)``. Never raises."""


class OpenAICompatibleClient:
    """The OpenAI-compatible shape most providers and every local server speak."""

    name = "openai"
    key_env = ""

    def complete(self, chat: ReviewerChat) -> tuple[str, str]:
        payload: dict[str, Any] = {
            "model": chat.model,
            "messages": [
                {"role": "system", "content": chat.system},
                {"role": "user", "content": chat.user},
            ],
            **temperature_field(chat.temperature),
            "max_tokens": chat.max_tokens,
            **chat.extra_body,
        }
        if chat.no_key_reason:
            return "", chat.no_key_reason
        key = chat.api_key
        body, err = chat.post_json(
            chat.base_url.rstrip("/") + "/chat/completions",
            payload,
            {"Authorization": f"Bearer {key}"} if key else {},
            chat.timeout_s,
        )
        if err:
            return "", chat.scrub(err)
        try:
            choice = body["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            return "", f"unexpected response shape: {str(body)[:300]}"
        content = (msg.get("content") or "").strip()
        if choice.get("finish_reason") == "length" and not chat.has_verdict(content):
            # Naming the remedy matters: "empty completion" sends an operator hunting for a
            # broken endpoint, when the endpoint is fine and the token budget is too small
            # for a model that thinks before it answers.
            used = (body.get("usage") or {}).get("completion_tokens_details") or {}
            return "", truncated_reason(
                chat.max_tokens, chat.temperature, reasoning_tokens=used.get("reasoning_tokens")
            )
        # Reasoning models split deliberation from the answer. We want the ANSWER; if the
        # model spent its whole budget reasoning and returned no content, that is an empty
        # completion (UNAVAILABLE), not a review that found nothing.
        return content, ""


class AnthropicClient:
    """Anthropic Messages API: system is a TOP-LEVEL field, not a message."""

    name = "anthropic"
    key_env = "ANTHROPIC_API_KEY"

    def complete(self, chat: ReviewerChat) -> tuple[str, str]:
        if not chat.api_key:
            return "", chat.no_key_reason
        body, err = chat.post_json(
            chat.base_url.rstrip("/") + "/messages",
            {
                "model": chat.model,
                "max_tokens": chat.max_tokens,
                **temperature_field(chat.temperature),
                "system": chat.system,
                "messages": [{"role": "user", "content": chat.user}],
                **chat.extra_body,
            },
            {"x-api-key": chat.api_key, "anthropic-version": "2023-06-01"},
            chat.timeout_s,
        )
        if err:
            return "", chat.scrub(err)
        try:
            blocks = body["content"]
        except (KeyError, TypeError):
            return "", f"unexpected response shape: {str(body)[:300]}"
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if body.get("stop_reason") == "max_tokens" and not chat.has_verdict(text):
            return "", truncated_reason(chat.max_tokens, chat.temperature)
        return text, ""


class GeminiClient:
    """Google generateContent: the key goes in a header, never the URL."""

    name = "gemini"
    key_env = "GEMINI_API_KEY"

    def complete(self, chat: ReviewerChat) -> tuple[str, str]:
        if not chat.api_key:
            return "", chat.no_key_reason
        # The key goes in a header, never the URL: a URL is echoed by proxies, access logs and
        # transport errors.
        body, err = chat.post_json(
            f"{chat.base_url.rstrip('/')}/models/{chat.model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": chat.system}]},
                "contents": [{"role": "user", "parts": [{"text": chat.user}]}],
                "generationConfig": {
                    **temperature_field(chat.temperature),
                    "maxOutputTokens": chat.max_tokens,
                },
                **chat.extra_body,
            },
            {"x-goog-api-key": chat.api_key},
            chat.timeout_s,
        )
        if err:
            return "", chat.scrub(err)
        try:
            cand = body["candidates"][0]
        except (KeyError, IndexError, TypeError):
            fb = (body or {}).get("promptFeedback", {})
            return "", f"no candidate returned{f' ({fb})' if fb else ''}"
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
        if cand.get("finishReason") == "MAX_TOKENS" and not chat.has_verdict(text):
            return "", truncated_reason(chat.max_tokens, chat.temperature)
        return text, ""


#: The wire-protocol clients, keyed by reviewer `name`. A new kind is one adapter registered
#: here -- lazily, so each module stays under the size ceiling -- never an edit to the review
#: service's dispatch.
CLIENTS: Registry[ReviewerClient] = Registry("reviewer kind", key=lambda client: client.name)
CLIENTS.register(OpenAICompatibleClient())
CLIENTS.register(AnthropicClient())
CLIENTS.register(GeminiClient())
