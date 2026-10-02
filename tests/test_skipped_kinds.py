"""A log with events from a NEWER ddflow says so (bug B168).

`fold(strict=False)` counted the kinds it skipped and nothing read the count, so an older
ddflow folded a newer log with events silently dropped. `doctor` and `status` now name each
unknown kind and how many were skipped -- the way an unknown config knob is named.
"""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.core.events import Event


def _newer_events(repo, kind: str, n: int) -> None:
    """Append ``n`` events of a kind this code does not know, straight to a shard (the
    append path refuses them -- they can only arrive by a merge from a newer checkout)."""
    shard = repo / ".ddflow" / "events" / "newer-agent.jsonl"
    with shard.open("a") as fh:
        for i in range(n):
            e = Event(kind=kind, subject="X", data={}, agent="newer-agent", lamport=900 + i, ts="t")
            e = Event(**{**e.__dict__, "id": e.compute_id()})
            fh.write(e.to_json() + "\n")


def test_doctor_names_unknown_kinds_and_counts(repo):
    run_cli(repo, "init")
    _newer_events(repo, "record.extended", 2)
    _newer_events(repo, "link.recorded", 1)
    _, out, _ = run_cli(repo, "doctor")
    assert "record.extended x2" in out and "link.recorded x1" in out
    assert "newer ddflow" in out


def test_doctor_is_silent_on_a_log_it_fully_understands(repo):
    run_cli(repo, "init")
    _, out, _ = run_cli(repo, "doctor")
    assert "newer ddflow" not in out


def test_status_carries_the_skipped_kinds(repo):
    run_cli(repo, "init")
    _newer_events(repo, "record.extended", 3)
    _, out, _ = run_cli(repo, "--json", "status")
    assert json.loads(out)["skipped_kinds"] == {"record.extended": 3}
