"""The timeout / missing-binary / failed-exit branches of every call site that went through
`infra.proc.capture` (B-uc-infra-proc-fsio).

Written against the sites as they were (each called `proc.run` itself) and unchanged by the
move: every site still reaches the process layer through the module attribute `proc.run`,
so one fake of it pins what each site makes of a timeout, a binary that is not there and a
non-zero exit. Real children for the handshakes, in one parametrized test.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.infra import forge, paths, signals
from ddflow.infra import proc as P
from ddflow.services import bisect, companions, jobs, onboard_verify, roborev


def _fake(monkeypatch: pytest.MonkeyPatch, outcome):
    """Make `proc.run` raise ``outcome`` (an exception) or return it (a result)."""
    calls: list[tuple] = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(P, "run", run)
    return calls


def _done(rc: int = 0, out: str = "", err: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


TIMEOUT = subprocess.TimeoutExpired(["x"], 1)


def test_forge_run_timeout_is_unavailable_and_a_missing_binary_propagates(monkeypatch, tmp_path):
    monkeypatch.setattr(forge.shutil, "which", lambda name: "/bin/" + name)
    _fake(monkeypatch, TIMEOUT)
    with pytest.raises(forge.ForgeUnavailable, match="timed out after 120s"):
        forge._run(tmp_path, ["gh", "pr", "list"])
    _fake(monkeypatch, FileNotFoundError("gh"))
    with pytest.raises(FileNotFoundError):
        forge._run(tmp_path, ["gh", "pr", "list"])
    done = _done(0, "ok")
    _fake(monkeypatch, done)
    assert forge._run(tmp_path, ["gh", "pr", "list"]).stdout == "ok"
    monkeypatch.setattr(forge.shutil, "which", lambda name: None)
    with pytest.raises(forge.ForgeUnavailable, match="is not installed"):
        forge._run(tmp_path, ["gh", "pr", "list"])


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (TIMEOUT, "vm_stat timed out after 2s"),
        (FileNotFoundError("vm_stat"), "vm_stat could not run: vm_stat"),
        (_done(3), "vm_stat exited 3"),
    ],
)
def test_signals_run_names_what_went_wrong(monkeypatch, outcome, message):
    _fake(monkeypatch, outcome)
    with pytest.raises(signals.Unavailable, match=message):
        signals._run(["vm_stat"], 2)
    _fake(monkeypatch, _done(0, "pages"))
    assert signals._run(["vm_stat"], 2) == "pages"


@pytest.mark.parametrize(
    ("outcome", "answer"),
    [(TIMEOUT, False), (FileNotFoundError("py"), False), (_done(1), False), (_done(0), True)],
)
def test_the_launch_python_probe_answers_a_bool_whatever_happens(monkeypatch, outcome, answer):
    _fake(monkeypatch, outcome)
    assert paths._imports_ddflow("python3", "/tmp") is answer


def test_a_probe_passes_fails_or_is_unknown(monkeypatch, tmp_path):
    def verdict(outcome):
        _fake(monkeypatch, outcome)
        runs: list = []
        probe = bisect.command_probe("pytest {tests}", tmp_path, runs=runs)
        return probe(["a"]), runs[0].detail

    assert verdict(_done(0)) == (True, "")
    ok, detail = verdict(_done(1, "boom\nlast line"))
    assert ok is False and detail == "exit 1: last line"
    assert verdict(TIMEOUT) == (None, "timed out after 600s")
    unknown, detail = verdict(FileNotFoundError("pytest"))
    assert unknown is None and detail.startswith("could not run: ")


def test_a_detection_probe_is_installed_not_installed_or_unknown(monkeypatch):
    monkeypatch.setattr(companions.shutil, "which", lambda name: "/bin/" + name)
    c = companions.Companion(id="x", detect=["x", "--version"])
    _fake(monkeypatch, _done(0, "x 1.0\n"))
    assert companions.is_installed(c) == (True, "x 1.0")
    _fake(monkeypatch, _done(2))
    assert companions.is_installed(c) == (False, "`x --version` exited 2")
    installed, why = (_fake(monkeypatch, TIMEOUT), companions.is_installed(c))[1]
    assert installed is None and "did not answer within" in why
    installed, why = (_fake(monkeypatch, OSError("nope")), companions.is_installed(c))[1]
    assert installed is None and why == "the probe could not be run at all (nope) — could not tell"


def test_launching_a_job_raises_what_the_process_layer_raised(monkeypatch, tmp_path):
    _fake(monkeypatch, TIMEOUT)
    with pytest.raises(subprocess.TimeoutExpired):
        jobs.launch("true", tmp_path, tmp_path / "log")
    _fake(monkeypatch, FileNotFoundError("sh"))
    with pytest.raises(FileNotFoundError):
        jobs.launch("true", tmp_path, tmp_path / "log")
    _fake(monkeypatch, _done(1, "", "bad"))
    with pytest.raises(RuntimeError, match="could not launch the job: bad"):
        jobs.launch("true", tmp_path, tmp_path / "log")
    _fake(monkeypatch, _done(0, "4242\n"))
    assert jobs.launch("true", tmp_path, tmp_path / "log") == 4242


@pytest.mark.parametrize("outcome", [FileNotFoundError("roborev"), TIMEOUT])
def test_roborev_that_cannot_be_asked_is_a_note_not_a_crash(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(roborev.shutil, "which", lambda name: "/bin/roborev")
    _fake(monkeypatch, outcome)
    review, note = roborev.review_of(tmp_path, "a" * 40)
    assert review is None and note.startswith(
        "could not ask roborev which agent reviewed aaaaaaaaaa"
    )
    _fake(monkeypatch, _done(1, "", "no repo"))
    assert roborev.review_of(tmp_path, "a" * 40)[1].endswith("(no repo)")


def test_onboard_probes_let_a_timeout_through_or_report_it(monkeypatch, tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    _fake(monkeypatch, TIMEOUT)
    with pytest.raises(subprocess.TimeoutExpired):
        onboard_verify._answers(tmp_path)
    check = onboard_verify._trailer_refused(tmp_path)
    assert check.outcome == "unavailable" and "the hook could not be probed" in check.detail
    _fake(monkeypatch, FileNotFoundError("hook"))
    assert onboard_verify._trailer_refused(tmp_path).outcome == "unavailable"
    calls = _fake(monkeypatch, _done(1, "", "x"))
    check = onboard_verify._answers(tmp_path)
    assert check.outcome == "failed" and "brief exited 1" in check.detail and len(calls) == 1


_SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        result = {"serverInfo": {"name": "fake-mcp", "version": "1"}, "protocolVersion": "x"}
        if msg["method"] == "tools/list":
            result = {"tools": [{"name": "a"}, {"name": "b"}]}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
    """
)


def _companion_handshake(script: Path, tmp_path: Path) -> tuple[bool | None, str]:
    c = companions.Companion(id="f", command=sys.executable, args=[str(script)])
    got = companions.verify_one(c, timeout_s=10)
    return got.speaks_mcp, got.detail


def _onboard_handshake(script: Path, tmp_path: Path) -> tuple[bool | None, str]:
    entry = {"command": sys.executable, "args": [str(script)]}
    got = onboard_verify._handshake(tmp_path, entry)
    return {"passed": True, "failed": False}.get(got.outcome), got.detail


@pytest.mark.parametrize(
    ("handshake", "detail"),
    [
        (_companion_handshake, "fake-mcp"),
        (_onboard_handshake, "fake-mcp answered initialize and listed 2 tools"),
    ],
)
def test_both_handshakes_get_an_answer_from_the_same_fake_server(tmp_path, handshake, detail):
    script = tmp_path / "server.py"
    script.write_text(_SERVER)
    ok, text = handshake(script, tmp_path)
    assert ok is True and detail in text
    # a binary that exits at once is not a server
    quit_ = tmp_path / "quit.py"
    quit_.write_text("import sys; sys.exit(0)\n")
    ok, _ = handshake(quit_, tmp_path)
    assert ok is False


def test_capture_reports_a_run_a_timeout_and_a_missing_binary_as_values(tmp_path):
    ok = P.capture([sys.executable, "-c", "print('hi')"], timeout=20)
    assert (ok.returncode, ok.stdout.strip(), ok.ran, ok.timed_out) == (0, "hi", True, False)
    assert ok.unwrap() is ok
    slow = P.capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
    assert slow.timed_out and not slow.ran and slow.returncode is None
    with pytest.raises(subprocess.TimeoutExpired):
        slow.unwrap()
    gone = P.capture([str(tmp_path / "no-such-binary")], timeout=5)
    assert isinstance(gone.error, FileNotFoundError) and not gone.timed_out
    with pytest.raises(FileNotFoundError):
        gone.unwrap()


def test_capture_detaches_stdin_unless_input_is_given():
    # In the MCP server stdin IS the protocol; a child must see end of file, not our pipe.
    read = [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"]
    assert P.capture(read, timeout=20).stdout.strip() == "0"
    assert P.capture(read, timeout=20, input="abc").stdout.strip() == "3"


def test_a_stdio_server_is_started_in_a_group_of_its_own_and_stopped_whole(tmp_path):
    server = P.spawn_stdio([sys.executable, "-c", "import time; time.sleep(60)"])
    assert server.poll() is None
    P.stop_group(server)
    assert server.poll() is not None


def test_scratch_helpers_leave_nothing_behind(tmp_path):
    from ddflow.infra import fsio

    with fsio.scratch_dir("u2-") as d:
        (d / "f").write_text("x")
        assert d.is_dir() and d.name.startswith("u2-")
    assert not d.exists()
    with fsio.temp_text("body", ".md") as path:
        assert path.read_text() == "body" and path.suffix == ".md"
    assert not path.exists()
    with pytest.raises(RuntimeError), fsio.temp_text("x") as path:
        raise RuntimeError
    assert not path.exists()
    with fsio.scratch_file() as fh:
        fh.write(b"abc")
        fh.seek(0)
        assert fh.read() == b"abc"
    free = fsio.unused_dir("u2-free-", tmp_path)
    assert free.parent == tmp_path and not free.exists()
