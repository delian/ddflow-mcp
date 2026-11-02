"""The callers that reach the network through the `HttpClient` port, on the fake.

Each keeps what it did before moving behind the port (same headers, caps, classification);
none opens a socket. The upstream duplicate check is in tests/test_upstream_dedupe.py.
"""

from __future__ import annotations

import pytest

from ddflow.adapters.fakes.http import FakeHttpClient
from ddflow.infra import release_index as RI
from ddflow.services import docscheck_net as N


def test_release_index_get_is_a_json_get_with_the_cap_and_timeout():
    http = FakeHttpClient().when("GET", "https://pypi.org/pypi/d/json", body=b'{"a": 1}')
    assert RI._get("https://pypi.org/pypi/d/json", 2.5, http) == b'{"a": 1}'
    (call,) = http.calls
    assert call.headers == {"Accept": "application/json"}
    assert (call.timeout, call.max_bytes, call.body) == (2.5, RI.MAX_BYTES, None)


@pytest.mark.parametrize("fail", ["timeout", "connection", "tls", "protocol"])
def test_release_index_get_failures_surface_as_one_error_via_newest(fail):
    http = FakeHttpClient().when(raises=fail)
    with pytest.raises(RI.ReleaseIndexError):
        RI.newest("d", fetch=lambda url, timeout: RI._get(url, timeout, http))


def test_release_index_error_status_is_a_release_index_error():
    http = FakeHttpClient().when(status=503)
    with pytest.raises(RI.ReleaseIndexError, match="503"):
        RI.newest("d", fetch=lambda url, timeout: RI._get(url, timeout, http))


def _texts(*urls: str) -> dict[str, str]:
    return {"README.md": "\n".join(f"See [x]({u}) for more." for u in urls) + "\n"}


def test_docs_links_gone_refused_silent_and_ok_are_told_apart():
    http = FakeHttpClient()
    http.when(url="https://e.test/ok", body="x").when(url="https://e.test/gone", status=404)
    http.when(url="https://e.test/410", status=410).when(url="https://e.test/403", status=403)
    http.when(url="https://e.test/down", raises="connection")
    urls = ["ok", "gone", "410", "403", "down"]
    res = N.check_remote(_texts(*(f"https://e.test/{u}" for u in urls)), 7, http)
    assert res.checked == 5
    assert {(f.ref, f.detail) for f in res.findings} == {
        ("https://e.test/gone", "the server answered 404"),
        ("https://e.test/410", "the server answered 410"),
    }
    assert "1 of 5 remote link(s) could not be reached" in res.unavailable
    assert "1 answered with an error" in res.unavailable


def test_docs_links_are_fetched_once_each_with_the_agent_and_no_body_read():
    http = FakeHttpClient().when(body="x")
    N.check_remote(_texts("https://e.test/a", "https://e.test/a", "https://e.test/b"), 9, http)
    assert [c.url for c in http.calls] == ["https://e.test/a", "https://e.test/b"]
    assert {c.method for c in http.calls} == {"GET"}
    assert all(c.headers == {"User-Agent": "ddflow-docs-check"} for c in http.calls)
    assert all((c.timeout, c.max_bytes) == (9, 0) for c in http.calls)


def test_docs_link_silence_carries_the_reason_not_a_stack_trace():
    http = FakeHttpClient().when(raises="connect_timeout")
    res = N.check_remote(_texts("https://e.test/a"), 1, http)
    assert "timed out" in res.unavailable and not res.findings


@pytest.mark.parametrize(
    ("script", "alive"),
    [
        ({"status": 200}, True),
        ({"status": 401}, True),  # a server answering 401/404 still is a server
        ({"status": 404}, True),
        ({"status": 500}, False),
        ({"raises": "connection"}, False),
        ({"raises": "timeout"}, False),
        ({"raises": "protocol"}, False),
    ],
)
def test_the_reviewer_url_probe_counts_any_non_5xx_answer_as_listening(monkeypatch, script, alive):
    from ddflow.services import review as R

    http = FakeHttpClient().when(**script)
    monkeypatch.setattr(R, "_client", lambda: http)
    assert R._url_ok("http://example.test/v1") is alive
    assert http.calls[0].raise_for_status is False
