"""Tracked transports, cancellation, and endpoint probing.

Split out of ``ddflow/services/review.py``: the ``_Cancel`` hedge-cancellation
machinery, the HTTP client accessor ``_client``, and ``probe_endpoint``/``detect``.
"""

from __future__ import annotations

import contextlib
import json
import threading
from typing import Any

from ...adapters.http import HttpError, default_client
from ...adapters.ports import HttpClient
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
            thing.abort()  # an HTTP connection handed over by the client (`on_connect`)
    except (OSError, ProcessLookupError):
        pass


#: The copy the current thread is running, if any: set by the review scheduler around
#: each `_chat`, read where a socket or process is created.
_running = threading.local()


def _attach_to_running(thing: Any) -> None:
    token = getattr(_running, "cancel", None)
    if token is not None:
        token.attach(thing)


def _client() -> HttpClient:
    """The HTTP client every reviewer call goes through (the `HttpClient` port's default
    adapter); a test swaps it for `FakeHttpClient`. Each call passes `on_connect=
    _attach_to_running`, so the winning hedged copy can cut a loser's socket."""
    return default_client()


def probe_endpoint(base_url: str, timeout_s: float = 4.0) -> list[str]:
    """Model ids served at ``base_url``, or [] if nothing answers.

    Deliberately short-timeout and exception-swallowing: this runs against a list of
    candidate ports, and a closed port must cost milliseconds, not a stack trace.
    """

    url = rewrite_localhost(base_url).rstrip("/") + "/models"
    try:
        resp = _client().request("GET", url, timeout=timeout_s, on_connect=_attach_to_running)
        body = json.loads(resp.body.decode("utf-8", "replace"))
    except (HttpError, ValueError, json.JSONDecodeError):
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
