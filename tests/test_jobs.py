"""Long-running jobs: the multi-hour runs an item is waiting on.

Most of the wall-clock on both projects ddflow is meant to take over is spent WAITING --
training runs, a 5M-record generation across an 8-replica model fleet -- and a queue that
knew only about files could not say whether that process was alive, finished, or killed
hours ago. Each test runs real processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import Job
from ddflow.services import jobs as J

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _jobs(repo: Path, *extra: str) -> list[dict]:
    _c, out, _e = run_cli(repo, "--json", "job", "list", *extra)
    return json.loads(out) if out.strip().startswith("[") else []


def test_a_job_is_launched_detached_watched_and_its_exit_code_collected(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "TRAIN", "--globs", "a.py")
    run_cli(repo, "claim", "TRAIN", "--no-worktree")
    # Held open on a file, not on a clock: a 1.5 s sleep raced the two CLI calls below,
    # and under a loaded suite the job had exited before `job end` asked (Bba19573760).
    # Bounded at 60 s, so a failing test leaves its job behind no longer than that.
    release = repo.parent / "release-job"
    hold = f"for i in $(seq 600); do [ -e {release} ] && break; sleep 0.1; done"
    code, out, err = run_cli(repo, "--json", "job", "run", "TRAIN", f"{hold}; echo done; exit 3")
    assert code == OK, err
    job = json.loads(out)
    assert [j["status"] for j in _jobs(repo)] == ["running"]

    code, _o, err = run_cli(repo, "job", "end", job["id"])
    assert code == REFUSED, "a running job was recorded as ended"

    release.touch()
    assert _wait(lambda: _jobs(repo)[0]["status"] == "exited")
    row = _jobs(repo)[0]
    assert row["exit_code"] == 3, "an exit code the command itself chose was lost"
    assert "done" in Path(job["log"]).read_text()

    code, out, err = run_cli(repo, "--json", "job", "end", job["id"], "--note", "loss 0.12")
    assert code == OK, err
    assert json.loads(out)["exit_code"] == 3
    assert _jobs(repo) == [], "an ended job is still listed as pending"
    assert _jobs(repo, "--all")[0]["status"] == "ended"


def test_a_killed_job_is_GONE_not_running_and_not_exited(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "GEN", "--globs", "a.py")
    run_cli(repo, "claim", "GEN", "--no-worktree")
    _c, out, _e = run_cli(repo, "--json", "job", "run", "GEN", "sleep 30")
    pid = json.loads(out)["pid"]
    os.kill(pid, 9)
    assert _wait(lambda: _jobs(repo)[0]["status"] == "gone")
    assert "killed" in _jobs(repo)[0]["detail"]


def test_the_job_survives_the_process_that_launched_it(repo):
    """Launched from a CLI process that has since exited -- the shape of a remote-control
    session being restarted mid-run."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    run_cli(repo, "claim", "T", "--no-worktree")
    _c, out, _e = run_cli(repo, "--json", "job", "run", "T", "sleep 3")
    pid = json.loads(out)["pid"]
    assert J.alive(pid), "the job died with the process that launched it"
    assert os.getsid(pid) == pid, "the job is not in a session of its own"
    os.kill(pid, 9)


def test_a_zombie_is_not_alive():
    """A finished child that nobody reaped still answers kill(pid, 0)."""
    p = subprocess.Popen(["true"])
    assert _wait(
        lambda: Path(f"/proc/{p.pid}/stat").read_text().rsplit(")", 1)[-1].split()[0] == "Z"
    )
    assert not J.alive(p.pid)
    p.wait()


def test_a_reused_pid_is_not_the_job():
    me = os.getpid()
    job = Job(id="J", pid=me, host=J.host(), proc_start="1", log="/nonexistent")
    assert J.status(job).state == "gone", "a live process with the job's pid was taken for it"
    same = Job(id="J", pid=me, host=J.host(), proc_start=J.proc_start(me), log="/nonexistent")
    assert J.status(same).state == "running"


def test_a_job_on_another_host_is_not_guessed_at():
    job = Job(id="J", pid=1, host="some-other-box", log="/nonexistent")
    assert J.status(job).state == "elsewhere"


def test_registering_a_process_requires_it_to_be_running(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    run_cli(repo, "claim", "T", "--no-worktree")
    p = subprocess.Popen(["sleep", "300"])
    try:
        code, _o, err = run_cli(
            repo, "job", "add", "T", "--pid", str(p.pid), "--command", "torchrun"
        )
        assert code == OK, err
    finally:
        p.kill()
        p.wait()
    code, _o, _e = run_cli(repo, "job", "add", "T", "--pid", "999999999")
    assert code == FAIL


def test_the_brief_tells_the_next_session_to_wait(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "TRAIN", "--globs", "a.py")
    run_cli(repo, "claim", "TRAIN", "--no-worktree")
    _c, out, _e = run_cli(repo, "--json", "job", "run", "TRAIN", "sleep 20")
    pid = json.loads(out)["pid"]
    try:
        _c, brief, _e = run_cli(repo, "brief")
        assert "## Long-running jobs" in brief
        assert "RUNNING" in brief and "do not start it again" in brief
    finally:
        os.kill(pid, 9)


def test_a_job_can_only_be_started_under_the_callers_own_claim(repo):
    """The claim is what checks files and resources; a job started around it runs on
    GPUs nobody granted. Found driving the MCP surface: `claim` refused for want of
    GPUs, and `job run` started the training anyway."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "TRAIN", "--globs", "a.py")
    code, _o, err = run_cli(repo, "job", "run", "TRAIN", "sleep 5")
    assert code == REFUSED and "claim TRAIN" in err + _o
    run_cli(repo, "claim", "TRAIN", "--no-worktree", agent="alice")
    code, _o, _e = run_cli(repo, "job", "run", "TRAIN", "sleep 5", agent="bob")
    assert code == REFUSED, "someone else's claim let bob start a job"


# -- roborev 828 / 833 ------------------------------------------------------------------


def test_job_add_refuses_without_the_callers_claim_too(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    p = subprocess.Popen(["sleep", "5"])
    try:
        code, _o, _e = run_cli(repo, "job", "add", "T", "--pid", str(p.pid))
        assert code == REFUSED, "job add registered a process under nobody's claim"
    finally:
        p.kill()
        p.wait()


def test_the_refusal_says_WAIT_when_someone_else_holds_the_item(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    run_cli(repo, "claim", "T", "--no-worktree", agent="alice")
    code, out, err = run_cli(repo, "job", "run", "T", "sleep 1", agent="bob")
    assert code == REFUSED
    assert "held by alice" in out + err and "claim T` first" not in out + err


def test_a_job_on_another_host_is_not_ended_without_force(repo):
    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    EventLog(repo, "x").append(
        "job.started", "J-remote", {"item": "T", "pid": 4242, "host": "other-box", "log": ""}
    )
    code, _o, _e = run_cli(repo, "job", "end", "J-remote")
    assert code == REFUSED, "'could not look' was recorded as 'ended'"
    code, out, err = run_cli(repo, "job", "end", "J-remote", "--force")
    assert code == OK, err
    assert "exit unknown" in out, "a job with no exit code printed 'exit None'"


def test_two_launches_in_one_second_do_not_share_a_log(repo):
    from ddflow.api import jobs as AJ

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T", "--globs", "a.py")
    run_cli(repo, "claim", "T", "--no-worktree", agent="w")
    # In-process and back to back: two CLI calls never land in the same second.
    logs = {AJ.job_run(repo, "T", "true", agent="w").data["log"] for _ in range(2)}
    assert len(logs) == 2, "two jobs wrote one log"


def test_an_exit_code_is_read_even_when_the_output_has_no_final_newline(repo, tmp_path):
    pid = J.launch("printf 'no newline'; exit 3", tmp_path, tmp_path / "j.log")
    job = Job(
        id="J", pid=pid, host=J.host(), proc_start=J.proc_start(pid), log=str(tmp_path / "j.log")
    )
    assert _wait(lambda: J.status(job).state != "running")
    assert J.status(job).state == "exited" and J.status(job).exit_code == 3
