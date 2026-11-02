"""The `HttpClient` port: the default adapter's behaviour and the fake's.

No network: the adapter's urllib calls are replaced by scripted ones (`ScriptedUrllib`), and
the fake never opens a socket. The same conformance cases run against both in
tests/test_ports_contract.py (`http_conformance`).
"""

from __future__ import annotations

import http.client
import io
import ssl
import urllib.error
import urllib.request

import pytest

from ddflow.adapters import http as H
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.http import ERRORS, FakeHttpClient, UnscriptedRequest
from ddflow.adapters.ports import HttpClient


class _Resp(io.BytesIO):
    """What `urlopen` returns: a context manager with status, headers and read(n)."""

    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        super().__init__(body)
        self.status, self.headers = status, headers

    def __exit__(self, *exc) -> None:
        self.close()


def _http_error(code: int, body: bytes = b"", headers: dict | None = None):
    msg = http.client.HTTPMessage()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("http://x/", code, "no", msg, io.BytesIO(body))


#: urllib-level failure per `ERRORS` name, so the adapter's classification is what is tested.
URLLIB_FAILURES = {
    "timeout": lambda: TimeoutError("timed out"),
    "connection": lambda: urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    "connect_timeout": lambda: urllib.error.URLError(TimeoutError("timed out")),
    "tls": lambda: urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")),
    "protocol": lambda: http.client.BadStatusLine(""),
    "incomplete": lambda: http.client.IncompleteRead(b"ab", 5),
}


class ScriptedUrllib:
    """The real client with `urllib.request.urlopen` scripted: same `when` as the fake."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[urllib.request.Request] = []
        self._rules: list[tuple[str | None, str | None, object]] = []
        self._client = H.UrllibHttpClient()
        monkeypatch.setattr(urllib.request, "urlopen", self._urlopen)

    def when(self, method=None, url=None, *, status=200, body=b"", headers=None, raises=None):
        if isinstance(body, str):
            body = body.encode()
        if raises is not None:
            made = URLLIB_FAILURES[raises]() if isinstance(raises, str) else raises
            outcome = made
        elif not 200 <= status < 300:
            outcome = lambda: _http_error(status, body, headers)  # noqa: E731 -- fresh fp per call
        else:
            outcome = lambda: _Resp(status, dict(headers or {}), body)  # noqa: E731
        self._rules.append((method, url, outcome))
        return self

    def _urlopen(self, req, timeout=None):
        self.calls.append(req)
        for method, url, outcome in self._rules:
            if method in (None, req.get_method()) and url in (None, req.full_url):
                if isinstance(outcome, BaseException):
                    raise outcome
                made = outcome()
                if isinstance(made, BaseException):
                    raise made
                return made
        raise AssertionError(f"unscripted {req.get_method()} {req.full_url}")

    def request(self, *args, **kwargs):
        return self._client.request(*args, **kwargs)


def http_conformance(c) -> None:
    """The cases an HttpClient must pass, whichever implementation: what a caller relies on."""
    c.when("GET", "http://a/ok", body=b"hello", headers={"X-A": "1"})
    c.when("GET", "http://a/big", body=b"0123456789")
    c.when("GET", "http://a/missing", status=404, body=b"nope")
    c.when("POST", "http://a/post", status=201, body=b"made")
    for name in ("timeout", "connection", "tls", "protocol", "incomplete"):
        c.when("GET", f"http://a/{name}", raises=name)

    r = c.request("GET", "http://a/ok", timeout=3)
    assert (r.status, r.body) == (200, b"hello") and r.headers["X-A"] == "1"
    assert c.request("GET", "http://a/big", timeout=3, max_bytes=4).body == b"0123"
    assert c.request("POST", "http://a/post", body=b"{}", timeout=3).status == 201

    with pytest.raises(H.HttpStatusError) as status:
        c.request("GET", "http://a/missing", timeout=3)
    assert (status.value.status, status.value.body) == (404, b"nope")
    assert isinstance(status.value, OSError)
    kept = c.request("GET", "http://a/missing", timeout=3, raise_for_status=False)
    assert (kept.status, kept.body) == (404, b"nope")

    for name, kind in (
        ("timeout", H.HttpTimeout),
        ("connection", H.HttpConnectionError),
        ("tls", H.HttpTlsError),
        ("protocol", H.HttpProtocolError),
        ("incomplete", H.HttpIncompleteRead),
    ):
        with pytest.raises(kind):
            c.request("GET", f"http://a/{name}", timeout=3)
    with pytest.raises(H.HttpSchemeError):
        c.request("GET", "file:///etc/passwd", timeout=3)
    with pytest.raises(OSError):  # every error of the family is an OSError
        c.request("GET", "http://a/connection", timeout=3)


# -- the default adapter ----------------------------------------------------------------


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(H.UrllibHttpClient(), HttpClient)
    assert isinstance(FakeHttpClient(), HttpClient)
    assert FAKES["HttpClient"] is FakeHttpClient
    assert H.default_client() is H.DEFAULT


def test_the_adapter_passes_the_conformance_cases(monkeypatch):
    http_conformance(ScriptedUrllib(monkeypatch))


def test_the_adapter_hands_urllib_the_method_url_headers_body_and_timeout(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen.update(
            method=req.get_method(),
            url=req.full_url,
            hdr=dict(req.header_items()),
            data=req.data,
            timeout=timeout,
        )
        return _Resp(200, {}, b"")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    H.UrllibHttpClient().request(
        "POST", "https://h/p", headers={"Content-Type": "application/json"}, body=b"{}", timeout=7.5
    )
    assert seen == {
        "method": "POST",
        "url": "https://h/p",
        "hdr": {"Content-type": "application/json"},
        "data": b"{}",
        "timeout": 7.5,
    }


def test_a_non_http_address_is_refused_before_urllib_is_asked(monkeypatch):
    def never(*a, **k):
        raise AssertionError("urlopen must not be called for file:")

    monkeypatch.setattr(urllib.request, "urlopen", never)
    with pytest.raises(H.HttpSchemeError, match="refusing 'file'"):
        H.UrllibHttpClient().request("GET", "file:///etc/passwd", timeout=1)


def test_a_malformed_address_is_an_http_error_not_a_value_error():
    with pytest.raises(H.HttpSchemeError, match="not a valid address"):
        H.UrllibHttpClient().request("GET", "http://[::1", timeout=1)
    with pytest.raises(H.HttpSchemeError):
        FakeHttpClient().request("GET", "http://[::1", timeout=1)


def test_a_status_urllib_did_not_follow_is_a_status_error_in_adapter_and_fake(monkeypatch):
    """304 is no 2xx: urllib raises HTTPError for it, so the client says HttpStatusError."""
    s = ScriptedUrllib(monkeypatch).when(url="http://a/n", status=304)
    f = FakeHttpClient().when(url="http://a/n", status=304)
    for c in (s, f):
        with pytest.raises(H.HttpStatusError) as exc:
            c.request("GET", "http://a/n", timeout=1)
        assert exc.value.status == 304


def test_the_tracking_opener_is_a_real_opener_and_hands_over_the_connected_socket(monkeypatch):
    """Built for real (handler classes and all), with only the TCP connect replaced."""
    seen = []

    class Sock:
        def sendall(self, data):
            raise OSError("test socket: not sending")

        def close(self):
            pass

    def connect(self):
        self.sock = Sock()

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
    for url in ("http://a.test/x", "https://a.test/x"):
        with pytest.raises(H.HttpConnectionError):
            H.UrllibHttpClient().request("GET", url, timeout=1, on_connect=seen.append)
    assert len(seen) == 2 and all(isinstance(h, H._Tracked) for h in seen)


def test_a_connect_timeout_is_a_connection_error_and_a_read_timeout_is_a_timeout(monkeypatch):
    s = ScriptedUrllib(monkeypatch)
    s.when(url="http://a/c", raises="connect_timeout").when(url="http://a/r", raises="timeout")
    with pytest.raises(H.HttpConnectionError) as c:
        s.request("GET", "http://a/c", timeout=1)
    assert c.value.timed_out and not isinstance(c.value, TimeoutError)
    assert str(c.value) == "<urlopen error timed out>"
    with pytest.raises(TimeoutError) as r:
        s.request("GET", "http://a/r", timeout=1)
    assert isinstance(r.value, H.HttpTimeout)


def test_the_text_urllib_gave_is_kept(monkeypatch):
    s = ScriptedUrllib(monkeypatch)
    s.when(url="http://a/x", raises="connection").when(url="http://a/p", raises="protocol")
    with pytest.raises(H.HttpConnectionError) as c:
        s.request("GET", "http://a/x", timeout=1)
    assert str(c.value) == "<urlopen error [Errno 111] Connection refused>"
    assert isinstance(c.value.reason, ConnectionRefusedError)
    with pytest.raises(H.HttpProtocolError) as p:
        s.request("GET", "http://a/p", timeout=1)
    assert p.value.cause.__class__ is http.client.BadStatusLine
    assert str(p.value) == repr(p.value.cause)


def test_a_body_failing_mid_read_is_classified(monkeypatch):
    class Cut(_Resp):
        def read(self, n=None):
            raise http.client.IncompleteRead(b"a", 9)

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Cut(200, {}, b""))
    with pytest.raises(H.HttpIncompleteRead):
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1)


def test_on_connect_uses_the_tracking_opener_and_abort_shuts_the_socket(monkeypatch):
    got = []

    class Opener:
        def open(self, req, timeout=None):
            return _Resp(200, {}, b"ok")

    made = []
    monkeypatch.setattr(H, "_tracked_opener", lambda cb: made.append(cb) or Opener())
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("plain path"))
    c = H.UrllibHttpClient()
    assert c.request("GET", "http://a/", timeout=1, on_connect=got.append).body == b"ok"
    assert len(made) == 1
    made[0]("handle")  # the opener's callback hands the connection on to the caller's
    assert got == ["handle"]

    shut: list[int] = []

    class Sock:
        def shutdown(self, how):
            shut.append(how)
            raise OSError("already closed")

    H._Tracked(Sock()).abort()  # an OSError from a closed socket is swallowed
    assert shut


# -- the fake ---------------------------------------------------------------------------


def test_the_fake_passes_the_conformance_cases():
    http_conformance(FakeHttpClient())


def test_the_fake_records_every_call_as_the_caller_made_it():
    f = FakeHttpClient().when(status=200)
    f.request("PUT", "http://a/x", headers={"A": "b"}, body=b"d", timeout=2.5, max_bytes=9)
    (call,) = f.calls
    assert (call.method, call.url, dict(call.headers), call.body) == (
        "PUT",
        "http://a/x",
        {"A": "b"},
        b"d",
    )
    assert (call.timeout, call.max_bytes, call.raise_for_status) == (2.5, 9, True)


def test_the_fake_matches_by_method_and_url_in_script_order_and_can_spend_a_rule():
    f = FakeHttpClient()
    f.when("GET", "http://a/", body="first", once=True).when("GET", "http://a/", body="second")
    f.when(None, "http://b/", prefix=True, body="any")
    assert [f.request("GET", "http://a/", timeout=1).body for _ in range(3)] == [
        b"first",
        b"second",
        b"second",
    ]
    assert f.request("DELETE", "http://b/deeper/path", timeout=1).body == b"any"
    with pytest.raises(UnscriptedRequest) as exc:
        f.request("POST", "http://a/", timeout=1)
    assert isinstance(exc.value, AssertionError) and "POST http://a/" in str(exc.value)
    assert len(f.calls) == 5  # the unscripted call is recorded too


@pytest.mark.parametrize("name", sorted(ERRORS))
def test_the_fake_raises_each_error_class(name):
    f = FakeHttpClient().when(raises=name)
    with pytest.raises(H.HttpError) as exc:
        f.request("GET", "http://a/", timeout=1)
    assert exc.value.kind in {"timeout", "connection", "tls", "scheme", "protocol", "incomplete"}


def test_the_fake_raises_an_exception_instance_as_given():
    boom = RuntimeError("x")
    with pytest.raises(RuntimeError):
        FakeHttpClient().when(raises=boom).request("GET", "http://a/", timeout=1)


def test_cancelling_a_hedged_copy_aborts_the_connection_the_client_handed_over():
    """`on_connect` hands the reviewer a handle with `abort()`; `_Cancel` calls it, also for a
    handle attached after the cancel, and a handle whose socket is already gone is no error."""
    from ddflow.services import review as R

    class Handle:
        aborted = 0

        def abort(self) -> None:
            Handle.aborted += 1

    token = R._Cancel()
    token.attach(Handle())
    token.cancel()
    token.attach(Handle())  # attached after cancellation: closed at once
    assert Handle.aborted == 2 and token.cancelled
    sock = type("S", (), {"shutdown": lambda self, how: (_ for _ in ()).throw(OSError("gone"))})()
    R._abort(H._Tracked(sock))


def test_the_fake_hands_on_connect_a_connection_that_records_abort():
    f = FakeHttpClient().when(body="x")
    got = []
    f.request("GET", "http://a/", timeout=1, on_connect=got.append)
    f.request("GET", "http://a/", timeout=1)
    assert got == f.connections and len(got) == 1  # only the request that asked got one
    got[0].abort()
    assert f.connections[0].aborted


def test_a_peer_that_hangs_up_mid_exchange_is_a_connection_error_not_a_protocol_one(monkeypatch):
    """`RemoteDisconnected` is an OSError AND an HTTPException; urlopen callers always saw the
    OSError branch ("unreachable"), so the client classifies it as a connection error, raised
    from the body read too (where urllib does not wrap it in a URLError)."""

    class Hangup(_Resp):
        def read(self, n=None):
            raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Hangup(200, {}, b""))
    with pytest.raises(H.HttpConnectionError) as exc:
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1)
    assert not isinstance(exc.value, H.HttpProtocolError)


def test_an_error_status_closes_its_response(monkeypatch):
    closed = []
    err = _http_error(500, b"boom")
    err.close = lambda: closed.append(True)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(err)
    )
    with pytest.raises(H.HttpStatusError):
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1)
    assert closed == [True]


def test_the_fake_returns_a_fresh_response_each_time():
    f = FakeHttpClient().when(headers={"A": "1"}, body="x")
    first = f.request("GET", "http://a/", timeout=1)
    first.headers["A"] = "changed"
    assert f.request("GET", "http://a/", timeout=1).headers["A"] == "1"


def test_the_fake_gives_no_connection_to_a_request_refused_before_connecting():
    f = FakeHttpClient().when(url="http://a/refused", raises="connection")
    f.when(url="http://a/cut", raises="incomplete").when(url="http://a/ok", body="x")
    got = []
    with pytest.raises(H.HttpConnectionError):
        f.request("GET", "http://a/refused", timeout=1, on_connect=got.append)
    assert got == []  # nothing connected
    with pytest.raises(H.HttpIncompleteRead):
        f.request("GET", "http://a/cut", timeout=1, on_connect=got.append)
    assert len(got) == 1  # connected, then the body was cut


def test_a_hangup_while_reading_the_body_is_a_connection_error_that_was_connected(monkeypatch):
    class Hangup(_Resp):
        def read(self, n=None):
            raise http.client.RemoteDisconnected("closed")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Hangup(200, {}, b""))
    with pytest.raises(H.HttpConnectionError) as read_phase:
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1)
    assert read_phase.value.connected
    s = ScriptedUrllib(monkeypatch).when(url="http://a/x", raises="connection")
    with pytest.raises(H.HttpConnectionError) as connect_phase:
        s.request("GET", "http://a/x", timeout=1)
    assert not connect_phase.value.connected
    # and the fake gives a handle to a failure of a connection that was up
    up = H.HttpConnectionError("closed")
    up.connected = True
    got = []
    with pytest.raises(H.HttpConnectionError):
        FakeHttpClient().when(raises=up).request(
            "GET", "http://a/", timeout=1, on_connect=got.append
        )
    assert len(got) == 1


def test_a_peer_that_hangs_up_before_any_response_is_connected_when_the_socket_was_handed_over(
    monkeypatch,
):
    """With the tracking opener, `on_connect` ran before the failure: `connected` says so."""

    class Opener:
        def open(self, req, timeout=None):
            self.cb(H._Tracked(object()))  # the socket came up ...
            raise http.client.RemoteDisconnected("closed before any response")  # ... then failed

    opener = Opener()

    def tracked(cb):
        opener.cb = cb
        return opener

    monkeypatch.setattr(H, "_tracked_opener", tracked)
    got = []
    with pytest.raises(H.HttpConnectionError) as exc:
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1, on_connect=got.append)
    assert exc.value.connected and len(got) == 1


def test_a_hangup_is_connected_even_when_no_on_connect_was_asked_for(monkeypatch):
    def hangup(req, timeout=None):
        raise urllib.error.URLError(http.client.RemoteDisconnected("closed before any response"))

    monkeypatch.setattr(urllib.request, "urlopen", hangup)
    with pytest.raises(H.HttpConnectionError) as exc:
        H.UrllibHttpClient().request("GET", "http://a/", timeout=1)
    assert exc.value.connected
