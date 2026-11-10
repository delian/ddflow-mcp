"""The default `HttpClient` adapter: the ONE module that speaks HTTP for ddflow.

Home of the `http` family (urllib.request, urllib.error, http.client, socket): the
`http-home` import contract lets only this module import them for HTTP. The port is
`HttpClient`, declared below next to its adapter; the fake is
`ddflow.adapters.fakes.http.FakeHttpClient`.

What it does is what `urllib.request.urlopen` did at every call site before, in one place:
the same proxies, redirects and TLS defaults, the caller's own timeout, http(s) only.
What it does not do: decide consent, read configuration, log, or retry. A caller keeps its
own consent and config checks and passes the timeout it already has as a knob.

Errors are classified into one family, all `OSError` subclasses so a caller that already
catches `OSError` keeps working, and each keeps the text urllib gave it:

* `HttpTimeout` (also a `TimeoutError`): the server accepted the request and did not answer in
  time (a read timeout). A CONNECT timeout is a `HttpConnectionError` with `timed_out`, as
  urllib wraps it in a `URLError`: nothing was listening.
* `HttpConnectionError`: nothing answered (refused, unreachable, DNS, a reset). Carries
  `reason`. `HttpTlsError` and `HttpSchemeError` are kinds of it.
* `HttpProtocolError`: an answer that is not valid HTTP or stopped mid-body
  (`http.client.HTTPException`); `HttpIncompleteRead` is the cut-short body.
* `HttpStatusError`: the server answered with a non-2xx status urllib did not follow (only raised when
  `raise_for_status` is true, the default); carries `status`, `headers`, `body`.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Protocol, runtime_checkable

from .ports import port

__all__ = ["HTTPStatus"]  # re-exported: status names for callers that may not import `http`

#: Statuses that are an answer; urllib turns every other one it does not follow (a 304, a 404)
#: into an `HTTPError`, which the client reports as `HttpStatusError`.
OK_STATUSES = range(200, 300)


@dataclass(frozen=True)
class HttpResponse:
    """A finished exchange: the status line's code, the headers and the body bytes."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


class HttpError(OSError):
    """Base of every error the client raises. `kind` names the class for a log line."""

    kind = "error"


class HttpConnectionError(HttpError):
    """Nothing answered. `reason` is urllib's (an `OSError`, a string); `timed_out` marks a
    connect timeout, which urllib reports as a `URLError`, not a `TimeoutError`; `connected`
    says the socket was up before it failed (a peer that hung up while the body was read)."""

    kind = "connection"

    def __init__(self, message: str, *, reason: Any = None, timed_out: bool = False) -> None:
        super().__init__(message)
        self.reason = reason if reason is not None else message
        self.timed_out = timed_out
        self.connected = False


class HttpTlsError(HttpConnectionError):
    """The TLS handshake or certificate check failed."""

    kind = "tls"


class HttpSchemeError(HttpConnectionError):
    """The address is not http(s): `file:` and friends are refused, never opened."""

    kind = "scheme"


class HttpTimeout(HttpError, TimeoutError):
    """The server accepted the request and did not answer within the timeout."""

    kind = "timeout"


class HttpProtocolError(HttpError):
    """A reply that is no valid HTTP, or stopped mid-body. `cause` is the original."""

    kind = "protocol"

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause
        self.reason = str(cause) if cause is not None else message


class HttpIncompleteRead(HttpProtocolError):
    """The body ended before its announced length (a socket shut mid-read)."""

    kind = "incomplete"


class HttpStatusError(HttpError):
    """The server answered with a failing status. `body` is what was read of it."""

    kind = "status"

    def __init__(self, status: int, headers: Mapping[str, str], body: bytes, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers
        self.body = body


def _classify(exc: BaseException) -> HttpError:
    """Map what urllib / http.client / the socket raised onto the family above."""
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return HttpTlsError(str(exc), reason=reason)
        return HttpConnectionError(
            str(exc), reason=reason, timed_out=isinstance(reason, TimeoutError)
        )
    if isinstance(exc, TimeoutError):
        return HttpTimeout(str(exc))
    if isinstance(exc, ssl.SSLError):
        return HttpTlsError(str(exc), reason=exc)
    if isinstance(exc, http.client.IncompleteRead):
        return HttpIncompleteRead(repr(exc), cause=exc)
    if isinstance(exc, OSError):  # incl. RemoteDisconnected, both an OSError and an HTTPException
        return HttpConnectionError(str(exc), reason=exc)
    if isinstance(exc, http.client.HTTPException):
        return HttpProtocolError(repr(exc), cause=exc)
    return HttpConnectionError(str(exc), reason=exc)


def _hung_up(exc: BaseException) -> bool:
    """A failure only an established connection can have: the peer accepted, then dropped it
    (`RemoteDisconnected`, a reset, a broken pipe), whether or not `on_connect` was asked for."""
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(
        cause, http.client.RemoteDisconnected | ConnectionResetError | BrokenPipeError
    )


def require_http_url(url: str) -> None:
    """Raise `HttpSchemeError` unless ``url`` is http(s): `urlopen` follows `file:` too, and
    an address from a config file must never read a local file (bandit B310)."""
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError as exc:  # e.g. an unterminated IPv6 literal
        raise HttpSchemeError(f"not a valid address: {exc}") from exc
    if scheme not in ("http", "https"):
        raise HttpSchemeError(f"only http(s) addresses; refusing {scheme!r}")


def local_endpoint(base_url: str) -> bool:
    """``True`` when ``base_url`` names a private/loopback/link-local address or host.

    Lives here (not a caller) so ``urllib.parse`` stays home in this module: the
    ``http-home`` import contract forbids every other module importing it."""

    try:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    except ValueError:  # e.g. an unterminated IPv6 literal: not a local endpoint
        return False
    if not host:
        return False
    # A literal address first: global means public, anything else (private/loopback/
    # link-local, or IPv6) reads from `is_global` so a global IPv6 literal is public.
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    # A hostname: a single label (localhost, ollama, vllm) is never a public internet
    # host, and a dotted name in a local pseudo-TLD is not routable beyond the machine.
    if "." not in host or host.endswith((".local", ".internal", ".lan", ".home.arpa")):
        return True
    return False


class _Tracked:
    """A connection's socket, offered to a caller that must be able to cut it off.

    `abort()` shuts the socket both ways, so the server sees the disconnect and a reader
    blocked on it wakes with an error; it is safe to call twice and after close.
    """

    def __init__(self, sock: Any) -> None:
        self._sock = sock

    def abort(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _tracked_opener(on_connect: Callable[[Any], None]) -> urllib.request.OpenerDirector:
    """`urlopen`'s own opener -- proxies, redirects, TLS defaults -- except that each
    connection hands its socket to `on_connect` once connected."""

    class _HTTP(http.client.HTTPConnection):
        def connect(self) -> None:
            super().connect()
            on_connect(_Tracked(self.sock))

    class _HTTPS(http.client.HTTPSConnection):
        def connect(self) -> None:
            super().connect()
            on_connect(_Tracked(self.sock))

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_HTTP, req)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_HTTPS, req, context=self._context)

    return urllib.request.build_opener(_HTTPHandler, _HTTPSHandler)


def _open(req: urllib.request.Request, timeout: float, on_connect):
    if on_connect is None:
        return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 -- scheme checked
    return _tracked_opener(on_connect).open(req, timeout=timeout)  # nosec B310 -- scheme checked


@port
@runtime_checkable
class HttpClient(Protocol):
    """One HTTP exchange, the way every caller makes it.

    Home: `ddflow.adapters.http` (urllib.request / http.client; the one module allowed to
    import them). Single adapter, `UrllibHttpClient`; the fake is `FakeHttpClient`. The
    adapter never decides consent or reads configuration: the caller checks both and passes
    its own timeout.
    """

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
        """Send the request; an `HttpResponse` (status, headers, body bytes). Raises only
        `HttpError` subclasses: `HttpTimeout`, `HttpConnectionError` (`HttpTlsError`,
        `HttpSchemeError`), `HttpProtocolError` and, unless `raise_for_status` is false,
        `HttpStatusError`. `on_connect` receives an object with `abort()` once connected."""


class UrllibHttpClient:
    """The default adapter: `HttpClient` over urllib.request / http.client. Stateless."""

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
        """One exchange. Raises only `HttpError` subclasses (see the module docstring).

        `max_bytes` caps the body read (None reads it all); `raise_for_status=False`
        returns a failing status as an `HttpResponse` instead of raising `HttpStatusError`;
        `on_connect` receives an object with `abort()` once the socket is connected.
        """
        require_http_url(url)
        req = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
        opened = False
        up = False  # the socket was handed over (only known when the caller asked for it)

        def connected(handle: Any) -> None:
            nonlocal up
            up = True
            on_connect(handle)  # type: ignore[misc]  # only installed when on_connect is set

        try:
            with _open(req, timeout, connected if on_connect is not None else None) as resp:
                opened = True
                return HttpResponse(resp.status, dict(resp.headers), resp.read(max_bytes))
        except urllib.error.HTTPError as exc:
            data = b""
            try:
                data = exc.read(max_bytes) if max_bytes is not None else exc.read()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                exc.close()  # release the socket now, not at garbage collection
            headers_out = dict(exc.headers or {})
            if not raise_for_status:
                return HttpResponse(exc.code, headers_out, data)
            raise HttpStatusError(exc.code, headers_out, data, str(exc)) from exc
        except (OSError, http.client.HTTPException) as exc:
            err = _classify(exc)
            if isinstance(err, HttpConnectionError):
                err.connected = opened or up or _hung_up(exc)
            raise err from exc


#: The one stateless instance callers default to; tests pass `FakeHttpClient` instead.
DEFAULT = UrllibHttpClient()


def default_client() -> UrllibHttpClient:
    """The client a caller uses when it was handed none."""
    return DEFAULT
