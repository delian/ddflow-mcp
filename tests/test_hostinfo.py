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
