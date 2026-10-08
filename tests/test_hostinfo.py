"""One hostname helper (D-unify, B-uni-identity.2-hostname): two forms, one comparison."""

from __future__ import annotations

import socket

import pytest

from ddflow.infra import hostinfo as H


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "box.example.org")


def test_the_two_forms(box):
    assert H.hostname() == "box.example.org"
    assert H.short_host() == "box"


def test_an_os_error_is_an_empty_name_not_a_crash(monkeypatch):
    def boom():
        raise OSError("no name")

    monkeypatch.setattr(socket, "gethostname", boom)
    assert H.hostname() == "" and H.short_host() == ""


@pytest.mark.parametrize(
    ("recorded", "same"),
    [
        ("box.example.org", True),  # the name this machine reports
        ("", True),  # no name recorded: not "elsewhere"
        ("box", False),  # a short name is not bridged: another site's `box` is elsewhere
        ("other.example.org", False),
        ("box.other.org", False),
    ],
)
def test_same_host_is_the_machines_own_name(box, recorded, same):
    assert H.same_host(recorded) is same


def test_a_machine_reporting_a_short_name_matches_its_own_record(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "box")
    assert H.same_host("box") is True
    assert H.same_host("box.example.org") is False


def test_stamps_keep_their_forms(box):
    """The compatibility note: waits and jobs keep recording the FULL name, the actor and
    agent id the SHORT one, as before; only the comparison learned both."""
    from ddflow.infra import log as L
    from ddflow.services import jobs

    assert jobs.host() == "box.example.org"
    assert L.bare_agent_id(".").startswith("box-")


def test_a_wait_of_this_machine_falls_through_to_its_pid_and_another_machines_does_not(box):
    """`Waiter.live` asks `same_host`: a wait stamped with this machine's name reads its pid
    (pid 0: gone), one stamped elsewhere is live on its deadline alone."""
    from ddflow.services import waits

    here = waits.Waiter(agent="a", pid=0, until=9e12, host="box.example.org")
    there = waits.Waiter(agent="a", pid=0, until=9e12, host="other.example.org")
    assert here.live() is False
    assert there.live() is True


def test_a_job_on_another_machine_is_elsewhere_and_this_ones_is_checked(box):
    from ddflow.services import jobs

    assert jobs.status(jobs.Job(id="j", pid=0, host="other.example.org")).state == "elsewhere"
    assert jobs.status(jobs.Job(id="j", pid=0, host="box.example.org")).state != "elsewhere"
    assert jobs.status(jobs.Job(id="j", pid=0, host="box")).state == "elsewhere"


def test_a_record_with_no_host_is_this_machines_to_check(box):
    """The compatibility note: a wait or job stored before `host` existed names no machine,
    and is checked here as before (the `host and ...` guard it used to carry lives in
    `same_host`)."""
    from ddflow.services import jobs, waits

    assert jobs.status(jobs.Job(id="j", pid=0, host="")).state != "elsewhere"
    assert waits.Waiter(agent="a", pid=0, until=9e12, host="").live() is False


def test_a_machine_that_cannot_name_itself_matches_no_record(monkeypatch):
    def boom():
        raise OSError("no name")

    monkeypatch.setattr(socket, "gethostname", boom)
    assert H.same_host("box.example.org") is False  # it cannot be shown to be this machine
    assert H.same_host("") is True


def test_a_human_approval_stamps_the_short_host(tmp_path):
    """`approve` records this machine's SHORT name, as it did before it used the helper."""
    import subprocess
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import run_cli

    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog

    repo = tmp_path / "p"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.plan_approved]\ntitle = "ok"\nhuman = true\nprompt = "ask"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "approve", "T1", "plan_approved")
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["plan_approved"]
    # The committed log redacts this machine's name (`[REDACTED:hostname]`); anything else
    # on disk must be the short form, never the full one.
    assert rec.evidence.get("host") in (H.short_host(), "[REDACTED:hostname]"), rec.evidence
