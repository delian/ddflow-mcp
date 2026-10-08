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


def test_a_human_approval_stamps_what_short_host_returns(tmp_path, monkeypatch):
    """`approve` stamps `hostinfo.short_host()`: a sentinel the log's hostname redaction
    cannot mask shows where the stamp comes from, and that it is the short form."""
    import subprocess
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import run_cli

    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services.gates import outcomes
    from ddflow.services.gates.defs import load_gates

    repo = tmp_path / "p"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.plan_approved]\ntitle = "ok"\nhuman = true\nprompt = "ask"\n'
    )
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    monkeypatch.setattr(H, "short_host", lambda: "sentinel-box")
    log = EventLog(repo)
    cfg = Config.load(repo)
    gates = load_gates(repo, cfg)
    outcomes.approve(log, cfg, "T1", "plan_approved", gates=gates)
    rec = fold(log.read_all(), strict=False).items["T1"].gates["plan_approved"]
    assert rec.evidence.get("host") == "sentinel-box", rec.evidence
