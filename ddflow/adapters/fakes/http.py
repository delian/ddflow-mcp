"""`FakeHttpClient`: a scripted, recording `HttpClient`. No socket, no urllib.

Script a reply per method and URL (`when`), give it an answer or any error class of the
real client, and read every call back from `calls`. It applies the same `raise_for_status`
rule as the default adapter, so a caller sees what it would see against a server. A request
nothing was scripted for raises `UnscriptedRequest` (an `AssertionError`): a test that
reaches an address it did not expect fails loudly instead of "the network was down".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..http import (
    OK_STATUSES,
    HttpConnectionError,
    HttpIncompleteRead,
    HttpProtocolError,
    HttpResponse,
    HttpSchemeError,
    HttpStatusError,
    HttpTimeout,
    HttpTlsError,
    require_http_url,
)

#: One error of each class the real client raises, by the name `raises=` takes.
ERRORS: dict[str, Callable[[str], BaseException]] = {
    "timeout": lambda url: HttpTimeout("timed out"),
    "connection": lambda url: HttpConnectionError(
        "<urlopen error [Errno 111] Connection refused>", reason="Connection refused"
    ),
    "connect_timeout": lambda url: HttpConnectionError(
        "<urlopen error timed out>", reason="timed out", timed_out=True
    ),
    "tls": lambda url: HttpTlsError("<urlopen error certificate verify failed>"),
    "scheme": lambda url: HttpSchemeError("only http(s) addresses"),
    "protocol": lambda url: HttpProtocolError("BadStatusLine('')"),
    "incomplete": lambda url: HttpIncompleteRead("IncompleteRead(0 bytes read)"),
}


class UnscriptedRequest(AssertionError):
    """The fake was asked for a method and URL nothing was scripted for."""


@dataclass(frozen=True)
class HttpCall:
    """One request the fake saw, as the caller made it."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float
    max_bytes: int | None
    raise_for_status: bool


class FakeConnection:
    """What `on_connect` receives from the fake: the handle a real client hands over, with
    `abort()` recorded instead of a socket shut."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


@dataclass
class _Rule:
    method: str | None
    url: str | None
    prefix: bool
    reply: HttpResponse | None
    error: BaseException | str | None
    once: bool
    used: int = 0


@dataclass
class FakeHttpClient:
    """Scripted replies in, recorded calls out. Rules are tried in the order they were
    added; the first whose method and URL match answers (a `once` rule is then spent)."""

    calls: list[HttpCall] = field(default_factory=list)
    #: One `FakeConnection` per request that was given an `on_connect`, in call order.
    connections: list[FakeConnection] = field(default_factory=list)
    _rules: list[_Rule] = field(default_factory=list)

    def when(
        self,
        method: str | None = None,
        url: str | None = None,
        *,
        status: int = 200,
        body: bytes | str = b"",
        headers: Mapping[str, str] | None = None,
        raises: BaseException | str | None = None,
        prefix: bool = False,
        once: bool = False,
    ) -> FakeHttpClient:
        """Script a reply. `method`/`url` None match anything; `prefix=True` matches the
        URL's start. `raises` is an exception to raise or a name in `ERRORS`."""
        reply = (
            None
            if raises is not None
            else HttpResponse(
                status, dict(headers or {}), body.encode() if isinstance(body, str) else body
            )
        )
        self._rules.append(_Rule(method, url, prefix, reply, raises, once))
        return self

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
        max_bytes: int | None = None,
        raise_for_status: bool = True,
        on_connect: Callable[[Any], None] | None = None,
    ) -> HttpResponse:
        self.calls.append(
            HttpCall(method, url, dict(headers or {}), body, timeout, max_bytes, raise_for_status)
        )
        require_http_url(url)
        if on_connect is not None:  # a real client calls it once the socket is connected
            self.connections.append(FakeConnection(url))
            on_connect(self.connections[-1])
        for rule in self._rules:
            if rule.once and rule.used:
                continue
            if rule.method is not None and rule.method != method:
                continue
            if rule.url is not None and not (
                url.startswith(rule.url) if rule.prefix else url == rule.url
            ):
                continue
            rule.used += 1
            return self._answer(rule, url, max_bytes, raise_for_status)
        raise UnscriptedRequest(f"no reply scripted for {method} {url}")

    @staticmethod
    def _answer(rule: _Rule, url: str, max_bytes: int | None, raise_for_status: bool):
        if rule.error is not None:
            err = ERRORS[rule.error](url) if isinstance(rule.error, str) else rule.error
            raise err
        reply = rule.reply
        assert reply is not None
        if max_bytes is not None:
            reply = HttpResponse(reply.status, reply.headers, reply.body[:max_bytes])
        if raise_for_status and reply.status not in OK_STATUSES:
            raise HttpStatusError(
                reply.status, reply.headers, reply.body, f"HTTP Error {reply.status}"
            )
        return reply
