"""`watch`: one call waits for a job, a file line, a process or a gate, renewing leases."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.api import watch_target
from ddflow.services import watch as W

ME = "agent-watcher"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=ME).ok
    return repo


def _watch(repo, **kw):
    kw.setdefault("timeout_s", 5)
    kw.setdefault("poll_s", 0.05)
    return watch_target(repo, agent=ME, **kw)


def test_a_file_line_is_returned_and_the_token_resumes_after_it(proj, tmp_path):
    f = tmp_path / "ci.out"
    f.write_text("start\nstep 1 ok\nDONE a\nmore\nDONE b\n")
    first = _watch(proj, file=str(f), until="^DONE")
    assert first.exit == 0 and first.data["line"] == "DONE a"
    again = _watch(proj, token=first.data["token"])
    assert again.exit == 0 and again.data["line"] == "DONE b"
    none = _watch(proj, token=again.data["token"], timeout_s=0)
    assert none.exit == 2 and none.data["timed_out"] is True


def test_a_half_written_line_is_not_matched_until_it_is_complete(proj, tmp_path):
    f = tmp_path / "x.log"
    f.write_text("DON")
    assert _watch(proj, file=str(f), until="^DONE$", timeout_s=0).exit == 2

    def finish():
        time.sleep(0.2)
        with f.open("a") as h:
            h.write("E\n")

    threading.Thread(target=finish, daemon=True).start()
    out = _watch(proj, file=str(f), until="^DONE$")
    assert out.exit == 0 and out.data["line"] == "DONE"


def test_a_process_is_awaited_until_it_exits(proj):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    t = threading.Thread(target=p.wait, daemon=True)
    t.start()
    out = _watch(proj, pid=p.pid)
    t.join(2)
    assert out.exit == 0 and out.data["matched"]


def test_a_job_reports_its_exit_status(proj):
    j = __import__("ddflow.api.jobs", fromlist=["x"]).job_run(
        proj, "T1", "echo hi; exit 3", agent=ME
    )
    assert j.ok, j.reason
    out = _watch(proj, job=j.data["id"])
    assert out.exit == 0 and out.data["exit_code"] == 3 and out.data["status"] == "exited"


def test_a_gate_is_awaited_until_it_has_an_outcome(proj):
    assert _watch(proj, gate="T1:implement", timeout_s=0).exit == 2
    run_cli(
        proj,
        "gate",
        "record",
        "T1",
        "implement",
        "--outcome",
        "passed",
        "--evidence",
        "x",
        "--agent",
        ME,
    )
    out = _watch(proj, gate="T1:implement")
    assert out.exit == 0 and out.data["outcome"] == "passed"


def test_the_leases_are_renewed_while_waiting(proj, tmp_path, monkeypatch):
    from ddflow.services import leasekeep as LK

    calls = []
    monkeypatch.setattr(LK, "throttled", lambda renew, *_a: lambda: calls.append(1) or renew())
    f = tmp_path / "y.log"
    f.write_text("")
    out = _watch(proj, file=str(f), until="never", heartbeat="T1", timeout_s=0.3)
    assert out.exit == 2 and calls, "the named lease was renewed on the waiting ticks"
    assert out.data["unrenewed"] == []


def test_a_lease_not_held_is_named_as_unrenewed(proj, tmp_path):
    f = tmp_path / "z.log"
    f.write_text("")
    out = _watch(proj, file=str(f), until="x", heartbeat="T9", timeout_s=0)
    assert out.data["unrenewed"] == ["T9"]


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"file": "f"},
        {"job": "J", "pid": 4},
        {"file": "f", "until": "("},
        {"gate": "nocolon"},
        {"token": "garbage!"},
    ],
)
def test_an_unwatchable_request_is_refused_with_a_reason(proj, kw):
    out = _watch(proj, **kw)
    assert out.exit == 1 and out.reason


def test_a_rewritten_file_is_read_again_from_the_start(proj, tmp_path):
    f = tmp_path / "r.log"
    f.write_text("aaaa\nbbbb\n")
    t = W.Target(file=str(f), until="zzz")
    assert W.scan_file(t) is None and t.offset == 10
    f.write_text("PASS\nFAIL\n")  # same size, new content
    t.until = "PASS"
    assert W.scan_file(t) == "PASS"


def test_a_longer_rewrite_with_the_same_header_is_read_again(proj, tmp_path):
    f = tmp_path / "h.log"
    header = "H" * 64 + "\n"
    f.write_text(header + "tail\n")
    t = W.Target(file=str(f), until="^MATCH")
    assert W.scan_file(t) is None
    f.write_text(header + "MATCH new\n" + "x" * 30 + "\n")  # longer, same first 64 bytes
    assert W.scan_file(t) == "MATCH new"


def test_a_token_with_extra_facets_or_junk_is_refused(proj, tmp_path):
    f = tmp_path / "t.log"
    f.write_text("")
    tok = _watch(proj, file=str(f), until="x", timeout_s=0).data["token"]
    assert _watch(proj, token=tok, until="y").exit == 1
    assert _watch(proj, token=tok, heartbeat="T1").exit == 1
    assert W.decode(base64.urlsafe_b64encode(b'{"offset":-1}').decode()) is None
    assert W.decode("W10") is None  # base64url of "[]"
    assert W.decode(base64.urlsafe_b64encode(b'{"pid":"-1"}').decode()) is None  # wrong-typed field
    me = os.getpid()
    assert _watch(proj, pid=me, timeout_s=float("nan")).exit == 1
    assert _watch(proj, pid=me, until="x", timeout_s=0).exit == 1


def test_a_timed_out_cli_watch_prints_its_token(proj, tmp_path):
    f = tmp_path / "c.log"
    f.write_text("")
    code, out, _ = run_cli(proj, "watch", "--file", str(f), "--until", "x", "--timeout", "0")
    assert code == 2 and "token " in out


def test_a_token_round_trips():
    t = W.Target(file="f", until="x", offset=7, heartbeat=["T1"])
    assert W.decode(W.encode(t)) == t
