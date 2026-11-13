# Adapter family `reviewer_clients`

Port: **ReviewerClient**, declared in `ddflow/adapters/reviewer_clients.py` and marked `@port`.

One completion to a model endpoint, shaped for its wire protocol: request/response shape,
auth header, error classification (quota, a leaked key, a 5xx, a timeout) and truncation
detection live here, behind the port, over the `HttpClient` port.

A multi-adapter family: each adapter is registered in `CLIENTS` under its `name`
(`openai`, `anthropic`, `gemini`); a new kind is one adapter in that registry, never another
`elif` in the review service.

- **Default adapters:** `OpenAICompatibleClient`, `AnthropicClient`, `GeminiClient`, keyed by `name` in `CLIENTS`. The `kind='command'` reviewer is not a wire protocol and is deliberately not a `ReviewerClient`.
- **Fake:** `FakeReviewerClient` in `ddflow/adapters/fakes/reviewer_clients.py`: scripts the `(content, error)` outcome and records the completion; no socket.
- **Conformance cases:** tests/test_adapter_reviewer_clients.py (port contract) and tests/test_adapters_reviewer_clients.py (each wire body).

The pattern is in [README.md](README.md).
