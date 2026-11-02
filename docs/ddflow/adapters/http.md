# Adapter family `http`

Port: **HttpClient**, declared in `ddflow/adapters/http.py` and marked `@port`.

One HTTP exchange, the way every caller makes it. Errors are one `OSError` family (`HttpTimeout`, `HttpConnectionError`, `HttpProtocolError`, `HttpStatusError`); the adapter never decides consent or reads config.

- **Default adapter:** `ddflow.adapters.http` (`UrllibHttpClient`; the only module allowed to import urllib, http.client and socket, see `.importlinter`).
- **Fake:** `FakeHttpClient` in `ddflow/adapters/fakes/http.py`: script a reply per method and URL with `when`, read every call back from `calls`; an unscripted request raises `UnscriptedRequest`.
- **Conformance cases:** tests/test_adapter_http.py.

The pattern is in [README.md](README.md).
