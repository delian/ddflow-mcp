"""A reviewer chunk that never converges is split, bounded and recorded (B587e607ad9,
B-review-chunk-overran-bound).

* B587e607ad9 -- a ~4.5k-char chunk got no answer within the timeout twice; a chunk lost to
  TIME (DID NOT CONVERGE) was never split the way a chunk lost to TOKENS was.
* B-review-chunk-overran-bound -- a request outlived the bound it stated, because the
  socket's read timeout restarts with every byte a loaded server sends; the bound is now
  a hard cancel.
"""

from __future__ import annotations

import json
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import review as R
from ddflow.services.review import PARTIAL, REVIEWED, UNAVAILABLE, Reviewer, review


def _two_files() -> str:
    return "".join(
        f"diff --git a/{n}.py b/{n}.py\n--- a/{n}.py\n+++ b/{n}.py\n@@ -0,0 +1 @@\n+x = '{m}'\n"
        for n, m in (("a", "FILEA"), ("b", "FILEB"))
    )


def _reviewer(tmp_path: Path, script: str, **kw) -> tuple[Reviewer, Path]:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(f"#!/bin/sh\nin=$(cat)\necho x >> '{seen}'\n{script}\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    kw.setdefault("hedge", 1)
    kw.setdefault("timeout_s", 1)
    return Reviewer(name="fake", kind="command", command=str(cli), model="m", **kw), seen


SLOW_WHEN_BOTH = (
    'case "$in" in *FILEA*FILEB*) sleep 20;; esac\n'
    "case \"$in\" in *FILEA*) printf 'FINDING HIGH a.py:1\\nbad a\\n\\nSTATUS: FINDINGS 1\\n';; "
    "*) echo 'STATUS: NO FINDINGS';; esac"
)


def test_a_chunk_that_never_converges_is_split_in_halves_and_reviewed(tmp_path):
    rev, seen = _reviewer(tmp_path, SLOW_WHEN_BOTH)
    res = review(rev, _two_files(), "i", split_rounds=1)
    assert res.status == REVIEWED, res.reason
    assert (res.chunks_reviewed, res.chunks_total) == (1, 1)
    assert len(seen.read_text().split()) == 3  # the whole chunk, then each half
    assert [f.severity for f in res.findings] == ["HIGH"]


def test_a_chunk_still_lost_after_the_rounds_is_recorded_partial_with_its_findings(tmp_path):
    slow_a = (
        'case "$in" in *FILEA*) sleep 20;; esac\n'
        "printf 'FINDING LOW b.py:1\\nnit\\n\\nSTATUS: FINDINGS 1\\n'"
    )
    rev, _ = _reviewer(tmp_path, slow_a)
    res = review(rev, _two_files(), "i", split_rounds=2)
    assert res.status == UNAVAILABLE and res.chunks_reviewed == 0
    gap = res.unreviewed[0]
    assert gap["partial"] == 1 and "1 reviewed" in gap["reason"], gap
    assert [f.severity for f in res.findings] == ["LOW"], "the reviewed half's finding is kept"


def test_split_rounds_one_is_a_single_second_draw(tmp_path):
    rev, seen = _reviewer(tmp_path, "sleep 20")
    res = review(rev, _two_files(), "i", split_rounds=1)
    assert res.status == UNAVAILABLE
    assert len(seen.read_text().split()) == 3  # whole + 2 halves, no quarters


def test_the_token_cap_lowers_the_request_budget_and_never_raises_it(monkeypatch):
    sent: list[int] = []

    def chat(rev, system, user, timeout_s, tree=None):
        sent.append(rev.max_tokens)
        return "STATUS: NO FINDINGS", ""

    monkeypatch.setattr(R, "_chat", chat)
    review(
        Reviewer(name="r", base_url="http://127.0.0.1:9/v1", model="m", max_tokens=32000, hedge=1),
        _two_files(),
        "i",
        chunk_max_tokens=4000,
    )
    review(
        Reviewer(name="r", base_url="http://127.0.0.1:9/v1", model="m", max_tokens=2000, hedge=1),
        _two_files(),
        "i",
        chunk_max_tokens=4000,
    )
    assert sent == [4000, 2000]


class _Trickle:
    """Answers 200 at once, then a byte every 0.3 s: the read timeout is renewed forever."""

    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    for _ in range(60):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.3)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


def test_a_request_is_cancelled_at_its_wall_clock_bound_even_if_bytes_keep_coming():
    srv = _Trickle()
    try:
        started = time.time()
        res = review(
            Reviewer(name="f", base_url=srv.url, model="m", timeout_s=2, hedge=1),
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n",
            "i",
        )
        took = time.time() - started
    finally:
        srv.server.shutdown()
    assert res.status == UNAVAILABLE
    assert took < 8, f"a 2 s bound took {took:.0f}s"
    assert json.dumps(res.unreviewed).lower().count("did not converge") >= 1, res.unreviewed
    assert PARTIAL != res.status


def test_partial_findings_follow_the_chunk_number_when_only_some_chunks_are_sent(tmp_path):
    """reviewer (refuted by this test): `partial` is keyed by position in the chunks sent, and
    absorbed by the same position; a --chunk 2 run keeps chunk 2's partial findings."""
    files = "".join(
        f"diff --git a/{n}.py b/{n}.py\\n--- a/{n}.py\\n+++ b/{n}.py\\n@@ -0,0 +1 @@\\n+x = '{m}'\\n"
        for n, m in (("a", "AAA"), ("b", "BBB"), ("c", "SLOWC"), ("d", "DDD"))
    ).replace("\\n", "\n")
    script = (
        'case "$in" in *SLOWC*) sleep 20;; esac\n'
        "case \"$in\" in *DDD*) printf 'FINDING LOW d.py:1\\nnit\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac"
    )
    rev, _ = _reviewer(tmp_path, script, max_chunk_chars=170)
    res = review(rev, files, "i", only=[2], split_rounds=2)
    assert res.chunks_total == 2 and res.reviewed == []
    gap = res.unreviewed[0]
    assert gap["chunk"] == 2 and gap["partial"] == 1, gap
    assert [(f.chunk, f.location) for f in res.findings] == [(2, "d.py:1")]
