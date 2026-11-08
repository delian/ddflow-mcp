"""`ddflow review --detach` runs the review in its OWN session and streams to a durable log,
so the harness's time limit kills only the launcher and the review outlives it (bug
Be927e56fb6): a background `ddflow review --delta` died at the 30 min limit with output only
"[killed]" and no recorded verdict. The launcher returns at once; the review's progress goes
to a log that survives a kill, so a cut run is never silent."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

# setsid and the /proc zombie check are POSIX; `_alive` reads /proc, so a POSIX host without
# it (macOS) must skip rather than Answer a zombie as alive.
pytestmark = pytest.mark.skipif(
    os.name != "posix" or not Path("/proc").exists(), reason="setsid and /proc are Linux"
)


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A reviewer that sleeps `slow` seconds when that file exists (its contents), else answers
    at once; branch `feat` with a change. Returns (the started marker, the reviewer's pidfile)."""
    began, pidfile = tmp_path / "began", tmp_path / "reviewer.pid"
    cli = tmp_path / "reviewer"
    cli.write_text(
        f"#!/bin/sh\ncat >/dev/null\nif [ -e '{tmp_path / 'slow'}' ]; then S=$(cat '{tmp_path / 'slow'}'); echo $$ > '{pidfile}'; touch '{began}'; sleep ${{S:-120}}; fi\n"
        "echo 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "r"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return began, pidfile


def _detach(repo: Path, *extra: str) -> dict:
    """Run `ddflow review --detach` and wait for the LAUNCHER; its `--json` body."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--json", "--repo", str(repo), "review", "T1",
         "--gate", "critic", "--branch", "feat", "--detach", *extra],
        capture_output=True, text=True, env=env, timeout=120,
    )  # fmt: skip
    assert p.returncode == 0, f"exit {p.returncode}: {p.stdout!r} {p.stderr!r}"
    return json.loads(p.stdout)


def _alive(pid: int) -> bool:
    """Is `pid` a live (non-zombie) process? `kill(pid, 0)` alone also answers for a zombie."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
        return raw.rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
        return True


def _read(log: Path) -> str:
    return log.read_text() if log.exists() else ""


def _gate(repo: Path) -> dict:
    _code, shown, _err = run_cli(repo, "--json", "show", "T1")
    return json.loads(shown).get("gates", {}).get("critic", {})


def _kill_reviewer(pidfile: Path, detached_pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, FileNotFoundError):
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(detached_pid, signal.SIGKILL)


def _started(began: Path, log: Path, pid: int) -> None:
    """The reviewer started AND the detached process is still alive: it outlived the launcher."""
    deadline = time.monotonic() + 60
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert began.exists(), f"the detached review never started: {_read(log)}"
    assert _alive(pid), {"pid": pid, "was": began.exists()}


def _wait_for_text(log: Path, needle: str, deadline: float) -> str:
    """The log's text once `needle` appears (a flushed line), or "" past the deadline."""
    while time.monotonic() < deadline:
        seen = _read(log)
        if needle in seen:
            return seen
        time.sleep(0.1)
    return _read(log)


def test_detach_returns_at_once_while_the_review_keeps_running(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    (tmp_path / "slow").write_text("120")
    body = _detach(repo)
    log = Path(body["log"])
    _started(began, log, body["pid"])
    _kill_reviewer(pidfile, body["pid"])


def test_a_killed_detached_review_leaves_its_streamed_output(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    (tmp_path / "slow").write_text("120")
    body = _detach(repo)
    pid, log = body["pid"], Path(body["log"])
    try:
        _started(began, log, pid)
        seen = _wait_for_text(log, "reviewing", time.monotonic() + 60)
        # Streamed BEFORE it was cut down, so a killed review is not a silent "[killed]".
        assert "→ r (" in seen and "reviewing" in seen, seen
    finally:
        _kill_reviewer(pidfile, pid)
    assert "reviewing" in _read(log), _read(log)


def test_a_detached_delta_review_records_passed_without_the_launcher(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    # The first review sets the head a delta diffs from; the follow-up is what --delta
    # detached re-checks, exactly the run the bug reported dying.
    assert run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat")[0] == 0
    (repo / "y.py").write_text("y = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "followup")
    (tmp_path / "slow").write_text("30")
    body = _detach(repo, "--delta")
    pid, log = body["pid"], Path(body["log"])
    try:
        # It outlived the launcher while its reviewer was still running, then recorded a
        # fresh DELTA round (not the fore-run's pass): delta_rounds rises only when the
        # detached reviewer actually returned a verdict.
        _started(began, log, pid)
        deadline = time.monotonic() + 90
        gate = {}
        while time.monotonic() < deadline and gate.get("evidence", {}).get("delta_rounds", 0) < 1:
            gate = _gate(repo)
            time.sleep(0.2)
        gate = _gate(repo)
        assert gate.get("outcome") == "passed", (gate, _read(log))
        assert gate.get("evidence", {}).get("delta_rounds", 0) >= 1, (gate, _read(log))
    finally:
        _kill_reviewer(pidfile, pid)


def test_relaunch_argv_strips_only_the_flag_never_a_positional_detach():
    from ddflow.surfaces.commands import review as CR

    assert CR._relaunch_argv(["--detach", "T1", "--delta"]) == ["T1", "--delta"]
    # a positional item literally named `--detach` past `--` is not the flag and stays
    assert CR._relaunch_argv(["--detach", "--", "--detach"]) == ["--", "--detach"]
    assert CR._relaunch_argv(["--", "--detach"]) == ["--", "--detach"]


def test_detach_argv_redeclares_the_resolved_identity_so_the_round_renews_the_lease():
    from ddflow.surfaces.commands import review as CR

    # A detached review relaunched with a DERIVED identity holds no lease to renew: the
    # holder's lease lapses over a long round (bug B-lease-ttl-expires-during-review). The
    # relaunch must re-declare the resolved id so the run heartbeats as the holder. It rides as
    # ONE attached token: `tests/test_review_identity_from_worktree.py` says why.
    assert CR._detach_argv(["--detach", "T1", "--delta"], "role") == [
        "--agent=role", "T1", "--delta",
    ]  # fmt: skip
    # a typed `--agent` is replaced by the resolved one, not duplicated
    assert CR._detach_argv(["--agent", "shell-alias", "T1", "--detach"], "role") == [
        "--agent=role", "T1",
    ]  # fmt: skip
    assert CR._detach_argv(["--agent=shell-alias", "T1", "--detach"], "role") == [
        "--agent=role", "T1",
    ]  # fmt: skip
    # naming nothing to relaunch is [] still, so `--detach` keeps its own-arguments refusal
    assert CR._detach_argv(["--detach"], "role") == []
    assert CR._detach_argv([], "role") == []
    # a positional arg past `--` is not a flag: it stays, and the resolved id leads
    assert CR._detach_argv(["--detach", "--", "--detach"], "role") == [
        "--agent=role", "--", "--detach",
    ]  # fmt: skip


def test_detach_relaunches_the_review_as_the_resolved_holder(monkeypatch):
    """`_detach` wires `_detach_argv` into the launched command, so the detached review runs
    as the holder and its long rounds renew the holder's lease (the `if not argv` fallback
    would launch the review under a derived id that holds no lease)."""
    from types import SimpleNamespace

    from ddflow.surfaces.commands import review as CR

    called: dict = {}

    def fake_detach(repo, item, command, *, cwd="", log_file="", agent=""):
        called.update(item=item, command=command, agent=agent)
        return SimpleNamespace(exit=0, data={"id": "j", "pid": 1, "log": "/tmp/l"}, reason="")

    monkeypatch.setattr(CR.AJ, "job_detach", fake_detach)
    a = SimpleNamespace(_argv=["--detach", "T1", "--gate", "critic"], id=["T1"])
    c = SimpleNamespace(
        repo=Path("."),
        called_from=Path("."),
        log=SimpleNamespace(agent_id="role"),
        out=lambda *a, **k: None,
    )
    assert CR._detach(a, c) == CR.OK
    assert "--agent=role" in called["command"], called
    assert called["agent"] == "role"


def test_detach_is_refused_for_verbs_that_run_no_review(repo, tmp_path):
    _setup(repo, tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    for verb in ("show", "triage"):
        p = subprocess.run(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "review", verb, "T1",
             "--detach"],
            capture_output=True, text=True, env=env, timeout=120,
        )  # fmt: skip
        assert p.returncode != 0, p.stdout
        assert "--detach" in p.stderr and "run no review" in p.stderr, p.stderr


def test_detach_note_names_a_stop_command_that_reaches_the_review():
    """`--detach` names how to STOP the review, not just a pid to `kill`. The pid it prints
    is the setsid SESSION leader (a `/bin/sh -c` wrapper): a plain `kill <pid>` kills only
    that wrapper and leaves the python review running, so the next `ddflow review` is
    refused with `already being reviewed (pid <review>)`. The note must name the whole
    process group the split detached into (bug B-review-detach-prints-launcher-pid)."""
    from ddflow.surfaces.commands import review as CR

    note = CR._detach_note(2618834, "/tmp/review.log")
    assert "review detached" in note
    # the stop command kills the group (negative pid), which is what `setsid` created: the
    # printed pid is the session leader, and only the group kill reaches the review itself.
    assert "kill -- -2618834" in note
    assert "/tmp/review.log" in note
