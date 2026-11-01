"""A legitimate history scrub and an old clock regression are permanent facts of an append-only
log; a PERSON accepts them once (`ddflow doctor --accept-history`) and `doctor` then says
`accepted` instead of PROBLEM, until the event changes again (B-doctor-accepted-history)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import identity as ID
from tests.conftest import run_cli

ACCEPT = ("doctor", "--accept-history", "--history-reason", "scrubbed a leaked key")


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *ID.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _shard(repo: Path) -> Path:
    # the person's shard (an agent that files an approval request writes its own)
    return next(
        p
        for p in sorted((repo / ".ddflow" / "events").glob("*.jsonl"))
        if b'"subject":"T1"' in p.read_bytes()
    )


def _edit(repo: Path, old: bytes, new: bytes) -> None:
    path = _shard(repo)
    raw = path.read_bytes()
    assert old in raw
    path.write_bytes(raw.replace(old, new, 1))


@pytest.fixture
def scrubbed(repo):
    """A project whose one task event was edited after the fact (the scrub's effect)."""
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "T1", "--title", "key-AAAA", "--globs", "t.py")[0] == 0
    _edit(repo, b"key-AAAA", b"[REDACTED]")
    return repo


def _doctor(repo: Path, *extra: str) -> tuple[int, str]:
    code, out, err = run_cli(repo, "doctor", *extra)
    return code, out + err


def test_an_accepted_scrub_is_a_note_not_a_problem_until_it_changes_again(scrubbed):
    code, text = _doctor(scrubbed)
    assert code == 1 and "content does not match its address" in text, text
    code, out, err = run_cli(scrubbed, *ACCEPT, "--history-note", "scrub commit abc123")
    assert code == 0, out + err
    code, text = _doctor(scrubbed)
    assert code == 0 and "PROBLEM" not in text, text
    assert "accepted" in text and "scrubbed a leaked key" in text, text
    # the same event id with different content is a PROBLEM again
    _edit(scrubbed, b"[REDACTED]", b"[REDACTED-2]")
    code, text = _doctor(scrubbed)
    assert code == 1 and "content does not match its address" in text, text
    # and a NEW mismatch is never covered by the earlier acceptance
    run_cli(scrubbed, "task", "add", "T2", "--title", "other", "--globs", "u.py")
    _edit(scrubbed, b'"title":"other"', b'"title":"edited"')
    code, text = _doctor(scrubbed)
    assert code == 1 and text.count("content does not match its address") == 2, text


def test_an_accepted_clock_regression_stays_specific(repo):
    run_cli(repo, "init")
    for n in (1, 2, 3):
        run_cli(repo, "task", "add", f"T{n}", "--title", f"t{n}", "--globs", f"{n}.py")
    code, text = _doctor(repo)
    assert code == 0, text
    path = _shard(repo)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # Lamport clock is part of the address, so build the regression with whole new events.
    from dataclasses import replace

    from ddflow.core.events import Event

    last = Event.from_json(json.dumps(rows[-1]))
    ev = replace(last, subject="TX", lamport=1, id="")
    ev = replace(ev, id=ev.compute_id())
    path.write_text(path.read_text() + ev.to_json() + "\n")
    code, text = _doctor(repo)
    assert code == 1 and "Lamport clock decreases" in text, text
    assert run_cli(repo, *ACCEPT)[0] == 0
    code, text = _doctor(repo)
    assert code == 0 and "Lamport clock decreases" in text and "accepted" in text, text
    assert "PROBLEM" not in text, text
    # The acceptance is SPECIFIC to that one decrease: a NEW one (a distinct shard, position
    # and clock values) is a PROBLEM again, not covered by the earlier acceptance.
    ev2 = replace(Event.from_json(json.dumps(rows[-1])), subject="TY", lamport=2, id="")
    ev2 = replace(ev2, id=ev2.compute_id())
    path.write_text(path.read_text() + ev2.to_json() + "\n")
    code, text = _doctor(repo)
    assert code == 1 and text.count("Lamport clock decreases") == 1 and "PROBLEM" in text, text


def test_accepting_requires_a_reason_and_records_nothing_without_one(scrubbed):
    code, _o, err = run_cli(scrubbed, "doctor", "--accept-history")
    assert code != 0 and "reason" in err.lower()
    assert _doctor(scrubbed)[0] == 1


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _approve(repo: Path, ask_id: str) -> None:
    st = fold(EventLog(repo, "probe").read_all(), strict=False)
    digest = asks_of(st)[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def test_an_agent_needs_a_persons_approval_answered_in_chat(scrubbed):
    code, out, err = run_cli(scrubbed, *ACCEPT, agent="bot")
    assert code == 3 and "--approval request" in out + err, out + err
    code, out, err = run_cli(scrubbed, *ACCEPT, "--approval", "request", agent="bot")
    assert code == 3, out + err
    ask = _ask_id(out + err)
    assert _doctor(scrubbed)[0] == 1  # a pending request records nothing
    # the agent cannot answer its own request, and an unanswered id is not an approval
    assert run_cli(scrubbed, *ACCEPT, "--approval", ask, agent="bot")[0] == 3
    assert _doctor(scrubbed)[0] == 1
    _approve(scrubbed, ask)  # the person, in chat
    code, out, err = run_cli(scrubbed, *ACCEPT, "--approval", ask, agent="bot")
    assert code == 0, out + err
    code, text = _doctor(scrubbed)
    assert code == 0 and "accepted" in text and "PROBLEM" not in text, text
    # the same approval does not cover a later edit of the event
    _edit(scrubbed, b"[REDACTED]", b"[REDACTED-2]")
    code, text = _doctor(scrubbed)
    assert code == 1 and "content does not match its address" in text, text
    assert run_cli(scrubbed, *ACCEPT, "--approval", ask, agent="bot")[0] == 3


@pytest.mark.parametrize("how", ["env", "harness"])
def test_an_agent_marker_is_an_agent_without_the_agent_flag(scrubbed, monkeypatch, how):
    if how == "env":
        monkeypatch.setenv("DDFLOW_AGENT", "bot")
    else:
        monkeypatch.setenv(ID.HARNESS_MARKERS[0], "1")
    code, out, err = run_cli(scrubbed, *ACCEPT)
    assert code == 3 and "--approval request" in out + err, out + err
    # restore the person identity for the follow-up read WITHOUT `monkeypatch.undo()`: that
    # also undoes `_a_person`'s deletions, re-instating a harness marker that is set when
    # ddflow's own tests run inside a harness (the wrong identity for what follows).
    for var in ("DDFLOW_AGENT", ID.HARNESS_MARKERS[0]):
        monkeypatch.delenv(var, raising=False)
    assert _doctor(scrubbed)[0] == 1  # nothing was recorded


def test_it_is_reachable_over_mcp_with_the_same_gate(scrubbed):
    from ddflow import api
    from ddflow.services.approvals.sites import L1, SITES, T2
    from ddflow.surfaces.declared.setup import COMMANDS

    ask = ("scrubbed", "", "", "")
    out = api.doctor(scrubbed, agent="bot", accept_history=True, history=ask)
    assert out.exit == 3, out.data
    doctor = next(c for c in COMMANDS if c.path == ("doctor",))
    names = {p.name: p for p in doctor.params}
    for n in ("accept_history", "history_reason", "history_note", "history_only", "approval"):
        assert not names[n].cli_only, n
    # The operator principle: the accept answers in chat, so the verb is declared at T2
    # (reachable without a terminal), not only its parameters as non-`cli_only`.
    accept = next(s for s in SITES if s.verb == "history.accept")
    assert accept.tier == T2 and accept.min_level == L1, accept
