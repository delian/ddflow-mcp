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
        ("box.example.org", True),  # the full form this machine reports
        ("box", True),  # the short form another ddflow stamped
        ("", True),  # no name recorded: not "elsewhere"
        ("other.example.org", False),
        ("other", False),
        ("box.other.org", False),  # two full names are equal or different machines
    ],
)
def test_same_host_compares_across_forms(box, recorded, same):
    assert H.same_host(recorded) is same


def test_a_machine_reporting_a_short_name_matches_a_full_record(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "box")
    assert H.same_host("box.example.org") is True
    assert H.same_host("other.example.org") is False


def test_stamps_keep_their_forms(box):
    """The compatibility note: waits and jobs keep recording the FULL name, the actor and
    agent id the SHORT one, as before; only the comparison learned both."""
    from ddflow.infra import log as L
    from ddflow.services import jobs

    assert jobs.host() == "box.example.org"
    assert L.bare_agent_id(".").startswith("box-")


def test_a_wait_stamped_short_is_still_this_machines(box):
    from ddflow.services import waits

    live = waits.Waiter(agent="a", pid=0, until=0.0, host="box")
    other = waits.Waiter(agent="a", pid=0, until=9e12, host="other.example.org")
    assert other.live() is True  # another machine: only its deadline speaks
    assert live.host == "box" and H.same_host(live.host)


def test_a_job_stamped_short_is_still_here(box):
    from ddflow.services import jobs

    assert jobs.status(jobs.Job(id="j", pid=0, host="box")).state != "elsewhere"
    assert jobs.status(jobs.Job(id="j", pid=0, host="other")).state == "elsewhere"
