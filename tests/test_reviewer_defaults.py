"""Reviewer defaults that fit a reasoning model (bugs B289bf87e8d, B568e9def3b, B769704d1dc).

* Temperature. Lesson Lf3feebf5d3 measured it: a reasoning model sampled at ddflow's old
  default of 0.3 looped ("let me reconsider") until `max_tokens` was spent, on every
  reasoning_effort tried; at its vendor's 1.0 the same diff reviewed fully. Every
  reviewer entry without its own `temperature` ran at 0.3. Unset now means the request
  carries NO temperature, so the server applies the model's own recommended sampling.
* Concurrency. `max_concurrency = 4` sent a 5-chunk x hedge-2 review in waves
  (1 301-1 465 s against 1 122 s in one wave). Unset now means one wave.
* Truncation. A chunk whose every copy ran out of budget mid-thought is tried once more,
  in halves when it splits, before it is reported unreviewed.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import review as R
from ddflow.services.review import PARTIAL, REVIEWED, Reviewer, review


class _Fake:
    """OpenAI-compatible. Records each request body and the peak requests in flight.

    A request whose prompt carries BOTH `HALF-A` and `HALF-B` is "too much thinking":
    it comes back `finish_reason: length` with no content, as a reasoning model that
    spent its budget does. `ALWAYS_TRUNC` truncates whatever else it carries. `DELAY`
    holds a request open so concurrency can be observed.
    """

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                text = body["messages"][-1]["content"]
                with fake.lock:
                    fake.bodies.append(body)
                    fake.in_flight += 1
                    fake.peak = max(fake.peak, fake.in_flight)
                try:
                    if m := re.search(r"DELAY=([\d.]+)", text):
                        threading.Event().wait(float(m.group(1)))
                    trunc = "ALWAYS_TRUNC" in text or ("HALF-A" in text and "HALF-B" in text)
                    choice = (
                        {"message": {"content": ""}, "finish_reason": "length"}
                        if trunc
                        else {
                            "message": {"content": "STATUS: NO FINDINGS"},
                            "finish_reason": "stop",
                        }
                    )
                    out = json.dumps(
                        {
                            "choices": [choice],
                            "usage": {"completion_tokens_details": {"reasoning_tokens": 100}},
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                finally:
                    with fake.lock:
                        fake.in_flight -= 1

        class S(ThreadingHTTPServer):
            request_queue_size = 64  # the default backlog of 5 staggers a burst of 10

        self.server = S(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


@pytest.fixture
def fake():
    f = _Fake()
    yield f
    f.server.shutdown()


def _files(*specs: str) -> str:
    return "".join(
        f"diff --git a/f{n}.py b/f{n}.py\n--- a/f{n}.py\n+++ b/f{n}.py\n@@ -0,0 +1 @@\n+# {s}\n"
        for n, s in enumerate(specs)
    )


def _two_hunks(a: str, b: str) -> str:
    return (
        "diff --git a/g.py b/g.py\n--- a/g.py\n+++ b/g.py\n"
        f"@@ -1 +1 @@\n+# {a}\n"
        f"@@ -40 +40 @@\n+# {b}\n"
    )


# -- temperature -----------------------------------------------------------------------


def test_an_unset_temperature_is_not_sent(fake):
    """The server's (the model's) own sampling applies, not a value ddflow picked."""
    res = review(Reviewer(name="r", base_url=fake.url, model="m"), _files("x"), "i")
    assert res.status == REVIEWED, res.reason
    assert fake.bodies and all("temperature" not in b for b in fake.bodies), fake.bodies[0]


def test_an_explicit_temperature_is_still_sent(fake):
    res = review(
        Reviewer(name="r", base_url=fake.url, model="m", temperature=1.0), _files("x"), "i"
    )
    assert res.status == REVIEWED, res.reason
    assert {b.get("temperature") for b in fake.bodies} == {1.0}


@pytest.mark.parametrize("kind", ["anthropic", "gemini"])
def test_other_backends_omit_an_unset_temperature(monkeypatch, kind):
    sent: list[dict] = []

    def post(url, payload, headers, timeout_s):
        sent.append(payload)
        return {}, "stop here"

    monkeypatch.setattr(R, "_post_json", post)
    monkeypatch.setenv("K", "key")
    for temperature, expect in ((None, False), (0.7, True)):
        sent.clear()
        rev = Reviewer(name="r", kind=kind, base_url="http://x", model="m", api_key_env="K")
        rev.temperature = temperature
        R._chat(rev, "s", "u", 5)
        gen = sent[0].get("generationConfig", sent[0])
        assert ("temperature" in gen) is expect, (kind, temperature, sent[0])


def test_truncated_names_temperature_as_a_remedy():
    msg = R._truncated(Reviewer(name="r"))
    assert "temperature" in msg and "max_tokens" in msg


# -- concurrency -----------------------------------------------------------------------


def test_by_default_every_chunk_and_copy_goes_out_in_one_wave(fake):
    rev = Reviewer(name="r", base_url=fake.url, model="m", max_chunk_chars=120, hedge=2)
    res = review(rev, _files(*["DELAY=1.5"] * 5), "i")
    assert res.status == REVIEWED, res.reason
    assert fake.peak == 10, f"5 chunks x hedge 2 ran {fake.peak} at a time, i.e. in waves"


def test_the_one_wave_is_bounded():
    assert R._concurrency(Reviewer(name="r", hedge=2), 1000) == R.AUTO_CONCURRENCY_CEILING
    assert R._concurrency(Reviewer(name="r", hedge=2), 3) == 6
    assert R._concurrency(Reviewer(name="r", hedge=2, max_concurrency=3), 5) == 3


# -- truncation ------------------------------------------------------------------------


def test_a_chunk_that_truncated_on_every_copy_is_retried_in_halves(fake):
    rev = Reviewer(name="r", base_url=fake.url, model="m", hedge=2)
    res = review(rev, _two_hunks("HALF-A", "HALF-B"), "i")
    assert res.status == REVIEWED, res.reason
    assert (res.chunks_reviewed, res.chunks_total) == (1, 1)
    sent = [b["messages"][-1]["content"] for b in fake.bodies]
    only_a = [c for c in sent if "HALF-A" in c and "HALF-B" not in c]
    only_b = [c for c in sent if "HALF-B" in c and "HALF-A" not in c]
    assert only_a and only_b, "both halves must be sent, each on its own"


def test_the_halves_replies_keep_every_finding(monkeypatch):
    """The parts' replies are joined into one; parse() reads every FINDING in it and
    one STATUS line, so neither half's findings are lost (critic, overruled by test)."""

    def reply(user: str) -> tuple[str, str]:
        for marker, sev in (("HALF-A", "HIGH"), ("HALF-B", "LOW")):
            if marker in user:
                return f"FINDING {sev} g.py:1\n{marker}\n\nSTATUS: FINDINGS 1", ""
        return "STATUS: NO FINDINGS", ""  # any other part split_diff produced

    monkeypatch.setattr(R, "_race", lambda rev, system, users, started: [reply(u) for u in users])
    out = R._retry_truncated(
        Reviewer(name="r"),
        "s",
        [_two_hunks("HALF-A", "HALF-B")],
        [("", "TRUNCATED: x")],
        lambda d, i: d,
        0.0,
    )
    findings, on_contract = R.parse(out[0][0])
    assert on_contract and out[0][1] == ""
    assert sorted(f.severity for f in findings) == ["HIGH", "LOW"]


def test_a_retry_that_truncates_again_stays_unreviewed_and_says_why(fake):
    rev = Reviewer(name="r", base_url=fake.url, model="m", hedge=1, max_chunk_chars=120)
    res = review(rev, _files("ALWAYS_TRUNC", "fine"), "i")
    assert res.status == PARTIAL, res.reason
    assert (res.chunks_reviewed, res.chunks_total) == (1, 2)
    assert "TRUNCATED" in res.reason
    tries = [b for b in fake.bodies if "ALWAYS_TRUNC" in b["messages"][-1]["content"]]
    assert len(tries) == 2, "an unsplittable truncated chunk is retried whole, once"


def test_halves_never_lose_a_file(monkeypatch):
    """A file section with no hunk, larger than half the chunk, used to be dropped by
    split_diff, so halves without it could stand in for the chunk. split_diff now keeps
    it (B779270c994's fix); whatever the retry sends, every file goes with it."""
    big = "diff --git a/big.bin b/big.bin\nrename from x\nrename to y\n" + "similarity 9\n" * 40
    chunk = _two_hunks("HALF-A", "HALF-B") + big
    seen: list[str] = []

    def race(rev, system, users, started):
        seen.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "_race", race)
    out = R._retry_truncated(
        Reviewer(name="r"), "s", [chunk], [("", "TRUNCATED: x")], lambda d, i: d, 0.0
    )
    sent = "".join(seen)
    for header in ("diff --git a/big.bin", "HALF-A", "HALF-B"):
        assert header in sent, f"the retry lost {header!r}"
    assert out[0][1] == ""


# -- a cancelled copy cut off mid-read (bug Bf948d29d37) -------------------------------


class _CutOff:
    """A response whose socket the winning copy shut while this one was reading it."""

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        import http.client

        raise http.client.IncompleteRead(b"", 154)


@pytest.mark.parametrize("kind", ["openai", "anthropic", "gemini"])
def test_a_response_cut_off_mid_read_is_an_error_not_a_crash(monkeypatch, kind):
    monkeypatch.setattr(R, "_open", lambda *a, **k: _CutOff())
    monkeypatch.setenv("K", "key")
    rev = Reviewer(name="r", kind=kind, base_url="http://x", model="m", api_key_env="K")
    content, err = R._chat(rev, "s", "u", 5)
    assert content == "" and "IncompleteRead" in err, err


@pytest.mark.parametrize("exc", ["InvalidURL", "BadStatusLine"])
def test_other_http_client_errors_are_named_and_never_retried_as_truncation(monkeypatch, exc):
    import http.client

    def boom(*a, **k):
        raise getattr(http.client, exc)("nonsense")

    monkeypatch.setattr(R, "_open", boom)
    content, err = R._chat(Reviewer(name="r", base_url="http://x", model="m"), "s", "u", 5)
    assert content == "" and exc in err and "cut off" not in err, err
    assert not err.startswith(R.TRUNCATED)


def test_a_headerless_chunk_is_retried_whole(monkeypatch):
    chunk = "@@ -1 +1 @@\n+# HALF-A\n@@ -40 +40 @@\n+# HALF-B\n" * 3
    seen: list[str] = []

    def race(rev, system, users, started):
        seen.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "_race", race)
    R._retry_truncated(
        Reviewer(name="r"), "s", [chunk], [("", "TRUNCATED: x")], lambda d, i: d, 0.0
    )
    assert seen == [chunk]
