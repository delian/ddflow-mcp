"""A review's chunks run in parallel, and hedged copies race (research Rfa535c6747).

Measured on the LAN reasoning model: review time is the model's reasoning length -- 64 s
to 391 s for the SAME 3 KB diff across 8 identical calls, with a tail that never answers
before `max_tokens` -- and chunks were sent one after another, so a 42 KB review took the
sum of 11 such draws (1 835 s) while the server sat idle. In parallel the same 24 KB
review took 483 s instead of 1 274 s.

The fake server below answers per chunk from markers in the chunk's own text, so every
test states exactly what each request does: answer after a delay, stall on its first
call, or never follow the contract. It records connections the client closed on it,
which is how a cancelled copy is told apart from one that was merely ignored.
"""

from __future__ import annotations

import json
import os
import re
import select
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.review import PARTIAL, REVIEWED, Reviewer, review

CONTRACT = "STATUS: NO FINDINGS"


class _Fake:
    """An OpenAI-compatible endpoint whose behaviour each chunk chooses for itself.

    `DELAY=<s>` answers after that many seconds; `STALL_FIRST` makes the first request
    carrying that marker hang (until the client hangs up); `OFF_CONTRACT` answers with
    no STATUS block; `FINDING=<tag>` reports one finding named <tag>.
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.hung_up = 0  # connections the client closed while we were still working
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                text = body["messages"][-1]["content"]
                key = re.search(r"CHUNK-(\w+)", text).group(1)
                with fake.lock:
                    fake.calls[key] = fake.calls.get(key, 0) + 1
                    nth = fake.calls[key]
                    fake.in_flight += 1
                    fake.peak = max(fake.peak, fake.in_flight)
                try:
                    delay = float(m.group(1)) if (m := re.search(r"DELAY=([\d.]+)", text)) else 0
                    if "STALL_FIRST" in text and nth == 1:
                        delay = 60
                    if fake._wait_or_hangup(self.connection, delay):
                        return
                    if "OFF_CONTRACT" in text:
                        reply = "I thought about it at length and have nothing to add."
                    elif m := re.search(r"FINDING=(\w+)", text):
                        reply = f"FINDING HIGH x.py:1\n{m.group(1)}\n\nSTATUS: FINDINGS 1"
                    else:
                        reply = CONTRACT
                    out = json.dumps(
                        {"choices": [{"message": {"content": reply}, "finish_reason": "stop"}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                finally:
                    with fake.lock:
                        fake.in_flight -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def _wait_or_hangup(self, conn, seconds: float) -> bool:
        """Sleep, but notice the client closing the connection -- a cancelled copy."""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([conn], [], [], min(0.05, max(0, end - time.time())))
            if ready and not conn.recv(1, 2):  # MSG_PEEK: b"" means the peer closed
                with self.lock:
                    self.hung_up += 1
                return True
        return False


@pytest.fixture
def fake():
    f = _Fake()
    yield f
    f.server.shutdown()


def _diff(*chunks: str) -> str:
    """One file per chunk; with a small max_chunk_chars each file is its own chunk."""
    return "".join(
        f"diff --git a/f{n}.py b/f{n}.py\n--- a/f{n}.py\n+++ b/f{n}.py\n@@ -0,0 +1 @@\n"
        f"+# CHUNK-c{n} {spec}\n"
        for n, spec in enumerate(chunks)
    )


def _rev(url: str, **kw) -> Reviewer:
    return Reviewer(name="fake", base_url=url, model="m", max_chunk_chars=120, **kw)


def test_chunks_are_reviewed_in_parallel(fake):
    started = time.time()
    res = review(_rev(fake.url, hedge=1, max_concurrency=4), _diff(*["DELAY=1"] * 4), "i")
    assert res.status == REVIEWED and res.chunks_reviewed == 4, res.reason
    assert time.time() - started < 2.5, "four 1 s chunks took the sum, not the slowest"
    assert fake.peak == 4


def test_max_concurrency_caps_requests_in_flight(fake):
    res = review(_rev(fake.url, hedge=1, max_concurrency=2), _diff(*["DELAY=0.3"] * 6), "i")
    assert res.status == REVIEWED, res.reason
    assert fake.peak <= 2


def test_a_hedged_copy_wins_and_the_stalled_one_is_cancelled(fake):
    """The runaway call is the tail this exists for: its sibling answers, the review
    completes in the sibling's time, and the stalled request is CLOSED -- the server
    sees the hang-up -- rather than left generating while the CLI waits on it."""
    started = time.time()
    res = review(_rev(fake.url, hedge=2, max_concurrency=4), _diff("STALL_FIRST"), "i")
    assert res.status == REVIEWED, res.reason
    assert time.time() - started < 5, "the review waited for the stalled copy"
    deadline = time.time() + 3
    while fake.hung_up == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert fake.hung_up == 1, "the losing copy was ignored, not cancelled"


def test_without_hedging_a_stalled_chunk_is_not_rescued(fake):
    """The control: the same stall with hedge=1 runs into the reviewer's timeout."""
    res = review(_rev(fake.url, hedge=1, timeout_s=2), _diff("STALL_FIRST"), "i")
    assert res.status != REVIEWED


def test_every_copy_off_contract_is_still_not_a_review(fake):
    res = review(_rev(fake.url, hedge=3), _diff("OFF_CONTRACT", ""), "i")
    assert res.status == PARTIAL, res.reason
    assert res.chunks_reviewed == 1 and res.chunks_off_contract == 1
    assert "OFF CONTRACT" in res.reason


def test_findings_come_back_in_chunk_order_whatever_finished_first(fake):
    res = review(
        _rev(fake.url, hedge=1, max_concurrency=3),
        _diff("FINDING=first DELAY=0.6", "FINDING=second DELAY=0.3", "FINDING=third"),
        "i",
    )
    assert res.status == REVIEWED, res.reason
    assert [f.detail.strip() for f in res.findings] == ["first", "second", "third"]


def test_a_command_reviewers_losing_copy_is_killed(tmp_path):
    """A CLI reviewer: the first copy sleeps (and records its pid), the second answers.
    The review returns in the second's time and the first's process is gone."""
    stamp = tmp_path / "first"
    pidfile = tmp_path / "pid"
    cli = tmp_path / "reviewer.sh"
    cli.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f'if mkdir "{stamp}" 2>/dev/null; then echo $$ > "{pidfile}"; exec sleep 60; fi\n'
        f"echo '{CONTRACT}'\n"
    )
    cli.chmod(0o755)
    started = time.time()
    res = review(
        Reviewer(name="cli", kind="command", command=str(cli), model="m", hedge=2),
        _diff(""),
        "i",
    )
    assert res.status == REVIEWED, res.reason
    assert time.time() - started < 10
    pid = int(pidfile.read_text())
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"the losing reviewer process {pid} is still running")


def test_the_defaults_parallelise_and_hedge():
    r = Reviewer(name="d")
    assert (r.hedge, r.max_concurrency) == (2, 4)


def test_no_chunks_is_no_results_not_a_crash():
    """Found by the critic review: ThreadPoolExecutor(max_workers=0) raises."""
    from ddflow.services.review import _race

    assert _race(Reviewer(name="d"), "system", [], time.time()) == []
