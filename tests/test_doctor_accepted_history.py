"""A legitimate history scrub and an old clock regression are permanent facts of an append-only
log; a PERSON accepts them once (`ddflow doctor --accept-history`) and `doctor` then says
`accepted` instead of PROBLEM, until the event changes again (B-doctor-accepted-history)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.services import identity as ID
from tests.conftest import run_cli

ACCEPT = ("doctor", "--accept-history", "--history-reason", "scrubbed a leaked key")


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *ID.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _shard(repo: Path) -> Path:
    (path,) = sorted((repo / ".ddflow" / "events").glob("*.jsonl"))
    return path


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


def test_accepting_requires_a_reason_and_records_nothing_without_one(scrubbed):
    code, _o, err = run_cli(scrubbed, "doctor", "--accept-history")
    assert code != 0 and "reason" in err.lower()
    assert _doctor(scrubbed)[0] == 1


@pytest.mark.parametrize("how", ["agent_flag", "env", "harness"])
def test_an_agent_identity_may_not_accept_history(scrubbed, monkeypatch, how):
    agent = "bot" if how == "agent_flag" else ""
    if how == "env":
        monkeypatch.setenv("DDFLOW_AGENT", "bot")
    if how == "harness":
        monkeypatch.setenv(ID.HARNESS_MARKERS[0], "1")
    code, out, err = run_cli(scrubbed, *ACCEPT, agent=agent)
    assert code == 3 and "person" in (out + err).lower(), out + err
    monkeypatch.undo()
    assert _doctor(scrubbed)[0] == 1  # nothing was recorded


def test_it_is_refused_over_mcp_and_is_not_an_mcp_parameter(scrubbed):
    from ddflow import api

    out = api.doctor(scrubbed, agent="bot", accept_history=True, history_reason="x")
    assert out.exit == 3, out.data
    from ddflow.surfaces.declared.setup import COMMANDS

    doctor = next(c for c in COMMANDS if c.path == ("doctor",))
    names = {p.name: p for p in doctor.params}
    assert names["accept_history"].cli_only and names["history_reason"].cli_only
