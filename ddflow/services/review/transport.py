"""Tracked transports, cancellation, and endpoint probing.

Split out of ``ddflow/services/review.py``: the ``_Cancel`` hedge-cancellation
machinery, the socket-tracked ``_open`` opener, and ``probe_endpoint``/``detect``.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...infra import proc as P
from ...infra.container import rewrite_localhost
from .model import WELL_KNOWN_ENDPOINTS


@contextlib.contextmanager
def _cancel_on_error(tokens):
    """On SIGTERM / Ctrl-C (or any error) end every request in flight before the pool's exit
    waits for its threads: a killed review hung for as long as the slowest reviewer
    (Bed9d75dd1c)."""
    try:
        yield
    except BaseException:
        for token in tokens.values():
            token.cancel()
        raise


class _Cancel:
    """What one hedged copy is blocked on, so the winning copy can stop it.

    A losing copy that is merely ignored keeps the server generating -- up to
    `max_tokens` of reasoning -- and keeps a thread the CLI must wait for before it can
    exit. So cancelling closes the thing it is blocked on: the socket of an HTTP
    reviewer (the server sees the disconnect and aborts the request), or the process
    group of a command reviewer. Things attached after cancellation are closed at once.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: list[Any] = []
        self.cancelled = False

    def attach(self, thing: Any) -> None:
        with self._lock:
            if not self.cancelled:
                self._held.append(thing)
                return
        _abort(thing)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            held, self._held = self._held, []
        for thing in held:
            _abort(thing)


def _abort(thing: Any) -> None:
    try:
        if isinstance(thing, P.Popen):
            P.kill_group(thing)  # the shell AND the reviewer it started
        else:
            thing.shutdown(socket.SHUT_RDWR)
    except (OSError, ProcessLookupError):
        pass


#: The copy the current thread is running, if any: set by the review scheduler around
#: each `_chat`, read where a socket or process is created.
_running = threading.local()


def _attach_to_running(thing: Any) -> None:
    token = getattr(_running, "cancel", None)
    if token is not None:
        token.attach(thing)


class _TrackedHTTP(http.client.HTTPConnection):
    def connect(self) -> None:
        super().connect()
        _attach_to_running(self.sock)


class _TrackedHTTPS(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        _attach_to_running(self.sock)


class _TrackedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_TrackedHTTP, req)


class _TrackedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_TrackedHTTPS, req, context=self._context)


#: `urlopen`'s own opener -- proxies, redirects, TLS defaults -- except that each
#: connection's socket is handed to the copy that opened it, so it can be cancelled.
_OPENER = urllib.request.build_opener(_TrackedHTTPHandler, _TrackedHTTPSHandler)


def _open(target: str | urllib.request.Request, *, timeout: float):
    """`urlopen`, for http(s) only. Every reviewer HTTP call goes through here.

    `urlopen` follows any scheme it knows, `file:` included, and reviewer URLs come from
    configuration: `base_url = "file:///..."` -- a typo, or a config someone else wrote --
    made `probe_endpoint` read a local file and report its contents as model ids
    (reproduced in tests/test_review_urls.py). Refused as a `URLError`, which every
    caller already treats as "unreachable". Flagged by bandit B310.
    """
    url = target.full_url if isinstance(target, urllib.request.Request) else target
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise urllib.error.URLError(f"only http(s) reviewer endpoints; refusing {scheme!r}")
    return _OPENER.open(target, timeout=timeout)  # nosec B310 -- scheme checked above


def probe_endpoint(base_url: str, timeout_s: float = 4.0) -> list[str]:
    """Model ids served at ``base_url``, or [] if nothing answers.

    Deliberately short-timeout and exception-swallowing: this runs against a list of
    candidate ports, and a closed port must cost milliseconds, not a stack trace.
    """

    url = rewrite_localhost(base_url).rstrip("/") + "/models"
    try:
        with _open(url, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return []
    return [m.get("id", "") for m in body.get("data", []) if m.get("id")]


def detect(
    endpoints: tuple[tuple[str, str], ...] = WELL_KNOWN_ENDPOINTS,
) -> list[tuple[str, str, list[str]]]:
    """(base_url, label, models) for every candidate endpoint that answers."""
    out = []
    for url, label in endpoints:
        models = probe_endpoint(url)
        if models:
            out.append((url, label, models))
    return out
