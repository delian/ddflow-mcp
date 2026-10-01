"""What a review sends, and what it says about what it could not review.

Each test is one filed bug, reproduced first against the unfixed code:

* B779270c994 -- a file larger than ``max_chunk_chars`` went out as a header-only chunk
  followed by its hunks, and reviewers reported "the new file has no content".
* Bbf41d8f07f -- git's hunk-header funcname guess (text after the closing ``@@``, often
  a line from ANOTHER block) reached the reviewer, which then cited it as the hunk's.
* Bd2332f8f2a -- a PARTIAL review named only the FIRST unreviewed chunk, and no files.
* B338a8bb598 -- a generation that outlived ``timeout_s`` read "unreachable: timed out",
  and a reply cut off at ``max_tokens`` mid-answer read "off contract".
* Bbeb0c7542f -- ``extra_rules`` had no caller and no config key.
* B9d8bd466c3 (part 1) -- nothing was printed while chunks were in flight.
* Bc6ec4fd40d / Bf0cccb8fb1 -- a 25-minute review did not renew the caller's lease.
"""

from __future__ import annotations

import json
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.log import EventLog
from ddflow.services.review import (
    PARTIAL,
    UNAVAILABLE,
    Reviewer,
    load_reviewers,
    review,
    split_diff,
)


def _new_file(name: str, lines: int) -> str:
    body = "".join(f"+line {i} " + "x" * 40 + "\n" for i in range(lines))
    return (
        f"diff --git a/{name} b/{name}\nnew file mode 100644\nindex 0000000..1111111\n"
        f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{lines} @@\n" + body
    )


def _command_reviewer(tmp_path: Path, script: str, **kw) -> tuple[Reviewer, Path]:
    """A kind=command reviewer running ``script`` with the prompt on stdin; every prompt
    it was sent is appended to the returned file."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(f"#!/bin/sh\nin=$(cat)\nprintf '%s\\n=====\\n' \"$in\" >> '{seen}'\n{script}\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    kw.setdefault("hedge", 1)
    return Reviewer(name="fake", kind="command", command=str(cli), model="m", **kw), seen


# -- B779270c994 ----------------------------------------------------------------------


@pytest.mark.parametrize("lines", [120, 200, 400])
def test_a_file_larger_than_a_chunk_is_never_sent_as_its_header_alone(lines):
    diff = _new_file("new.py", lines)
    chunks = split_diff(diff, 5000)
    assert chunks, "nothing to review"
    for c in chunks:
        assert c.startswith("diff --git a/new.py"), "a chunk lost its file header"
        assert "\n@@ " in c, f"a chunk carries no hunk -- a header-only chunk: {c[-80:]!r}"


def test_hunks_of_a_large_file_are_split_and_each_keeps_the_header():
    hunks = "".join(
        f"@@ -{i * 10 + 1},2 +{i * 10 + 1},2 @@\n-old {i}\n+new {i} {'y' * 300}\n"
        for i in range(40)
    )
    diff = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n" + hunks
    chunks = split_diff(diff, 2000)
    assert len(chunks) > 1
    assert all(c.startswith("diff --git a/big.py") and "\n@@ " in c for c in chunks)
    # Every hunk is sent exactly once.
    sent = "".join(c.split("+++ b/big.py\n", 1)[1] for c in chunks)
    assert sent == hunks


def test_a_large_file_section_with_no_hunk_is_still_sent():
    renames = "".join(f"rename from old/{i}.py\nrename to new/{i}.py\n" for i in range(20))
    diff = "diff --git a/old.py b/new.py\nsimilarity index 100%\n" + renames + _new_file("n.py", 3)
    chunks = split_diff(diff, 200)
    assert any("rename from old/0.py" in c for c in chunks), "a hunkless section was dropped"


# -- Bbf41d8f07f ----------------------------------------------------------------------


def test_hunk_header_context_never_reaches_the_reviewer(tmp_path):
    rev, seen = _command_reviewer(tmp_path, "echo 'STATUS: NO FINDINGS'")
    diff = (
        "diff --git a/companions.toml b/companions.toml\n--- a/companions.toml\n"
        "+++ b/companions.toml\n@@ -104,11 +106,12 @@ default = true\n"
        " [memory]\n-default = true\n+default = false\n"
    )
    res = review(rev, diff, "memory is off by default")
    assert res.chunks_reviewed == 1, res.reason
    prompt = seen.read_text()
    assert "@@ -104,11 +106,12 @@\n" in prompt, "the hunk header itself must still be sent"
    assert "@@ default = true" not in prompt, "git's funcname guess reached the reviewer"
    assert "+default = false" in prompt


# -- Bd2332f8f2a ----------------------------------------------------------------------


def _three_files(bad: tuple[int, ...]) -> str:
    return "".join(
        f"diff --git a/f{n}.py b/f{n}.py\n--- a/f{n}.py\n+++ b/f{n}.py\n@@ -0,0 +1 @@\n"
        f"+x = {n}  # {'BADCHUNK' if n in bad else 'ok'} {'p' * 60}\n"
        for n in range(1, 4)
    )


BAD_OFF_CONTRACT = (
    "case \"$in\" in *BADCHUNK*) echo 'I thought about it.';; *) echo 'STATUS: NO FINDINGS';; esac"
)


def test_every_unreviewed_chunk_is_named_with_its_files(tmp_path):
    rev, _ = _command_reviewer(tmp_path, BAD_OFF_CONTRACT, max_chunk_chars=150)
    res = review(rev, _three_files(bad=(2, 3)), "add three files")
    assert res.status == PARTIAL and res.chunks_total == 3 and res.chunks_reviewed == 1
    assert "chunk 2/3" in res.reason and "chunk 3/3" in res.reason, res.reason
    assert "f2.py" in res.reason and "f3.py" in res.reason, res.reason
    assert "f1.py" not in res.reason, "a reviewed chunk is listed as unreviewed"
    gaps = res.evidence()["unreviewed"]
    assert [g["chunk"] for g in gaps] == [2, 3]
    assert [g["files"] for g in gaps] == [["f2.py"], ["f3.py"]]
    assert all(g["reason"] for g in gaps)


def test_a_fully_reviewed_result_lists_nothing_unreviewed(tmp_path):
    rev, _ = _command_reviewer(tmp_path, BAD_OFF_CONTRACT, max_chunk_chars=150)
    res = review(rev, _three_files(bad=()), "add three files")
    assert res.chunks_reviewed == 3 and res.evidence()["unreviewed"] == []


# -- B338a8bb598 ----------------------------------------------------------------------


class _Slow:
    """An OpenAI-compatible endpoint that accepts the request and answers as told."""

    def __init__(self, delay: float, reply: dict) -> None:
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                time.sleep(delay)
                out = json.dumps(reply).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n"


def test_a_generation_that_outlives_the_timeout_is_not_called_unreachable():
    slow = _Slow(3, {"choices": [{"message": {"content": "STATUS: NO FINDINGS"}}]})
    try:
        res = review(
            Reviewer(name="f", base_url=slow.url, model="m", timeout_s=1, hedge=1), DIFF, "i"
        )
    finally:
        slow.server.shutdown()
    assert res.status == UNAVAILABLE
    assert "unreachable" not in res.reason, res.reason
    assert "did not converge" in res.reason.lower() and "timeout_s" in res.reason, res.reason


def test_a_dead_endpoint_is_still_unreachable():
    res = review(
        Reviewer(name="f", base_url="http://127.0.0.1:9/v1", model="m", timeout_s=2, hedge=1),
        DIFF,
        "i",
    )
    assert res.status == UNAVAILABLE and "unreachable" in res.reason, res.reason


def test_a_reply_cut_off_at_max_tokens_is_truncated_not_off_contract():
    cut = {
        "choices": [
            {
                "message": {"content": "Let me reconsider the loop once more..."},
                "finish_reason": "length",
            }
        ],
        "usage": {"completion_tokens": 32000},
    }
    slow = _Slow(0, cut)
    try:
        res = review(Reviewer(name="f", base_url=slow.url, model="m", hedge=1), DIFF, "i")
    finally:
        slow.server.shutdown()
    assert res.status == UNAVAILABLE
    assert "OFF CONTRACT" not in res.reason, res.reason
    assert "TRUNCATED" in res.reason and "max_tokens" in res.reason, res.reason


# -- Bbeb0c7542f ----------------------------------------------------------------------


def test_extra_rules_reach_the_reviewers_system_prompt(tmp_path):
    rev, seen = _command_reviewer(
        tmp_path, "echo 'STATUS: NO FINDINGS'", extra_rules="NEVER-FLAG-VENDORED-CODE"
    )
    review(rev, DIFF, "i")
    prompt = seen.read_text()
    assert "Project-specific rules" in prompt and "NEVER-FLAG-VENDORED-CODE" in prompt


def test_extra_rules_load_from_the_reviewer_block(repo):
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text(
        '[[reviewer]]\nname = "r"\nbase_url = "http://x/v1"\nmodel = "m"\n'
        'extra_rules = """\nOnly report defects in ddflow/.\n"""\n'
    )
    (r,) = load_reviewers(repo)
    assert "Only report defects in ddflow/." in r.extra_rules


# -- api: progress (B9d8bd466c3 part 1) and the caller's lease (Bc6ec4fd40d, Bf0cccb8fb1) --


def _api_repo(repo: Path, tmp_path: Path, script: str) -> None:
    cli = tmp_path / "slow-reviewer"
    cli.write_text(f"#!/bin/sh\nin=$(cat)\n{script}\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[lease]\nheartbeat_s = 1\n[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\nmax_chunk_chars = 150\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")


SLOW_SECOND = "case \"$in\" in *SLOWCHUNK*) sleep 3.5;; esac\necho 'STATUS: NO FINDINGS'"


def test_review_renews_the_callers_lease_while_it_waits(repo, tmp_path, monkeypatch):
    import ddflow.api.review as api
    from ddflow.services import leases as L

    _api_repo(repo, tmp_path, SLOW_SECOND)
    run_cli(repo, "claim", "T1", agent="worker")
    (repo / "a.py").write_text("x = 1  # SLOWCHUNK\n")
    threads = []
    real = L.renew
    monkeypatch.setattr(
        L, "renew", lambda *a, **k: (threads.append(threading.current_thread()), real(*a, **k))[1]
    )
    before = sum(1 for e in EventLog(repo).read_all() if e.kind == "lease.renewed")
    out = api.review(repo, gate="critic", item="T1", agent="worker")
    assert out.data["outcome"] == "passed", out.reason
    renewed = [e for e in EventLog(repo).read_all() if e.kind == "lease.renewed"]
    assert len(renewed) - before >= 2, "nothing renewed the lease while the review ran"
    assert {e.data.get("holder") for e in renewed} == {"worker"}
    assert set(threads) == {threading.main_thread()}, "renewed from another thread"


def test_a_bystanders_review_does_not_renew_a_lease_it_does_not_hold(repo, tmp_path):
    import ddflow.api.review as api

    _api_repo(repo, tmp_path, SLOW_SECOND)
    run_cli(repo, "claim", "T1", agent="worker")
    (repo / "a.py").write_text("x = 1  # SLOWCHUNK\n")
    before = sum(1 for e in EventLog(repo).read_all() if e.kind == "lease.renewed")
    api.review(repo, gate="critic", item="T1", agent="bystander")
    after = sum(1 for e in EventLog(repo).read_all() if e.kind == "lease.renewed")
    assert after == before, "a bystander's review renewed someone else's lease"


def test_progress_is_reported_as_each_chunk_settles(repo, tmp_path, monkeypatch):
    import ddflow.api.review as api

    _api_repo(repo, tmp_path, SLOW_SECOND)
    (repo / "a.py").write_text("x = 1  # quick " + "p" * 60 + "\n")
    (repo / "b.py").write_text("y = 2  # SLOWCHUNK " + "p" * 60 + "\n")
    lines: list[tuple[float, str]] = []
    started = time.time()
    out = api.review(
        repo,
        gate="critic",
        item="T1",
        on_progress=lambda line: lines.append((time.time() - started, line)),
    )
    assert out.data["outcome"] == "passed", out.reason
    quick = [t for t, line in lines if "chunk" in line and "a.py" in line]
    assert quick and quick[0] < 2.5, f"the quick chunk was reported only at the end: {lines}"
    assert any("waiting" in line for _t, line in lines), f"no word while waiting: {lines}"
