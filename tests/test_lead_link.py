"""The lead-to-lane channel: `ddflow lanes lead`, the ETA, and the ask/answer relay.

A lead keeps the main task and the operator conversation; lanes are workers. These tests
pin the lead's side: one read-only view that names each lane's state and holder with an
honest ETA (a floor in open items from the schedule's critical path, never a fabricated
wall-clock), and a message/answer relay that writes one inbox and never sweeps.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.services.lanes import lead as LD


@pytest.fixture
def chained(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "A root", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "B leaf", "--globs", "b.py", "--needs", "A")
    assert run_cli(repo, "claim", "A", "--no-worktree", agent="holder")[0] == 0
    return repo


def test_lead_view_names_states_holders_and_eta(chained):
    code, out, _ = run_cli(chained, "lanes", "lead", agent="lead")
    assert code == 0
    assert "holder" in out and " A " in out and "working" in out
    # A is the root of the only open chain: B still waits on it, so there is an estimate.
    assert "critical path" in out


def test_lead_view_is_exit_two_with_no_lanes(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "lanes", "lead", agent="lead")[0] == 2


def test_answer_refuses_without_a_pending_ask(repo):
    run_cli(repo, "init")
    _msg, why = LD.answer(repo, "lane", "do it", sender="lead", item="A")
    assert _msg is None and "no pending ask" in why


def test_answer_relays_once_an_ask_is_pending(repo):
    run_cli(repo, "init")
    ask, why = LD.message(repo, "lead", "which item?", sender="lane", item="A")
    assert ask is not None, why
    reply, why = LD.answer(repo, "lane", "take A", sender="lead", item="A")
    assert reply is not None, why
    got = LD.pending(repo, "lane")
    assert [m.text for m in got] == ["take A"]


def test_answer_ignores_an_ask_for_a_different_item(repo):
    run_cli(repo, "init")
    assert LD.message(repo, "lead", "which?", sender="lane", item="A")[0] is not None
    assert LD.answer(repo, "lane", "take B", sender="lead", item="B")[0] is None


def test_answer_ignores_a_wake_that_is_not_a_question(repo):
    run_cli(repo, "init")
    assert (
        LD.message(repo, "lead", "your item merged", sender="lane", item="A", reason="merge_offer")[
            0
        ]
        is not None
    )
    assert LD.answer(repo, "lane", "thanks", sender="lead", item="A")[0] is None


def test_answer_carries_the_asks_item_when_answer_omits_it(repo):
    run_cli(repo, "init")
    assert LD.message(repo, "lead", "which?", sender="lane", item="A")[0] is not None
    reply, why = LD.answer(repo, "lane", "take A", sender="lead")
    assert reply is not None and reply.item == "A", why
