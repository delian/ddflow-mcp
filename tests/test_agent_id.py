"""An agent id is checked where it is used, and its shard name is injective (bug Bc598dc2d6a).

The id is the operator's own word (`--agent`, `DDFLOW_AGENT`, `as_agent`) and becomes a file
name in the committed log: a hostile or careless one must get a clear refusal, never a
traceback from the file system, and two different ids must never share a shard.
"""

from __future__ import annotations

import pytest

from ddflow.core import ids as I
from ddflow.infra.log import EventLog


def _notes(log: EventLog) -> list[str]:
    return [e.data["text"] for e in log.read_all() if e.kind == "session.note"]


def _write(tmp_path, agent: str) -> EventLog:
    log = EventLog(tmp_path, agent)
    log.append("session.note", "s1", {"text": "hello", "item": ""})
    return log


@pytest.mark.parametrize("agent", ["x" * 300, "é" * 130, "a" * 129])
def test_an_overlong_id_is_refused_with_a_message_not_an_oserror(tmp_path, agent):
    with pytest.raises(ValueError, match="agent id") as err:
        _write(tmp_path, agent)
    assert str(I.AGENT_ID_MAX) in str(err.value)
    assert not (tmp_path / ".ddflow" / "events").exists() or not list(
        (tmp_path / ".ddflow" / "events").glob("*")
    )


@pytest.mark.parametrize(
    "agent",
    [
        "a\nb",
        "a\x00b",
        "tab\there",
        "bell\x07",
        "del\x7f",
        "next\x85line",
        "ls\u2028x",
        "ps\u2029x",
    ],
)
def test_an_id_with_a_control_character_is_refused(tmp_path, agent):
    with pytest.raises(ValueError, match="agent id"):
        _write(tmp_path, agent)


@pytest.mark.parametrize("agent", ["a" * 128, "é" * 100])
def test_an_id_at_the_cap_is_accepted_and_its_shard_name_fits_the_file_system(tmp_path, agent):
    log = _write(tmp_path, agent)
    (shard,) = log.shards()
    assert len(shard.name.encode()) <= 255
    assert _notes(log) == ["hello"]


def test_ids_that_the_old_naming_merged_get_distinct_shards(tmp_path):
    ids = ["a/b", "a b", "a:b", "a_b", "a?b", "a\\b"]
    names = {EventLog(tmp_path, a).shard.name for a in ids}
    assert len(names) == len(ids), names


def test_an_id_that_needs_no_change_keeps_its_old_shard_name(tmp_path):
    # r-replay-compat: a log written under the old naming keeps its file and its meaning
    for agent in ("a_b", "Monster3-ddflow-7267fe", "reviewer-2", "host.local-123"):
        assert EventLog(tmp_path, agent).shard.name == f"{agent}.jsonl"


@pytest.mark.parametrize("agent", [".", "..", ".hidden", "...", "../../etc/x", "/abs/path"])
def test_a_hostile_id_stays_inside_the_events_directory_and_is_read_back(tmp_path, agent):
    log = _write(tmp_path, agent)
    (shard,) = log.shards()  # a name starting with a dot would be skipped by the shard glob
    assert shard.parent == tmp_path / ".ddflow" / "events"
    assert not shard.name.startswith(".")
    assert _notes(log) == ["hello"]


def test_two_agents_whose_ids_were_merged_no_longer_share_a_file(tmp_path):
    first = _write(tmp_path, "a/b")
    second = _write(tmp_path, "a b")
    assert first.shard != second.shard
    assert len(first.shards()) == 2
    assert {e.agent for e in first.read_all() if e.kind == "session.note"} == {"a/b", "a b"}


def test_the_shard_stem_is_deterministic():
    assert I.shard_stem("a/b") == I.shard_stem("a/b")
    assert I.shard_stem("a/b") != I.shard_stem("a:b")


def test_the_command_line_names_the_problem_and_prints_no_traceback(tmp_path):
    import subprocess
    import sys

    cmd = ["--repo", str(tmp_path), "--agent", "x" * 300, "session", "start"]
    out = subprocess.run(
        [sys.executable, "-m", "ddflow", *cmd], capture_output=True, text=True, timeout=120
    )
    text = out.stdout + out.stderr
    assert out.returncode != 0
    assert "Traceback" not in text and "OSError" not in text
    assert "agent id" in text and "128" in text


@pytest.mark.parametrize("agent", ["a" * 49, "a" * 128, "x" * 100 + ".y-z_1"])
def test_a_long_id_the_old_naming_accepted_keeps_its_shard_so_old_logs_replay(tmp_path, agent):
    assert EventLog(tmp_path, agent).shard.name == f"{agent}.jsonl"


@pytest.mark.parametrize("agent", ["a" * 128, "\u00e9" * 124, "\u6f22" * 83])
def test_a_stem_of_up_to_249_bytes_keeps_its_name(tmp_path, agent):
    assert len(agent.encode()) <= 249
    assert EventLog(tmp_path, agent).shard.name == f"{agent}.jsonl"


@pytest.mark.parametrize("agent", ["\u00e9" * 125, "\u6f22" * 84])
def test_a_stem_over_249_bytes_gets_a_digest_name_that_fits(tmp_path, agent):
    name = EventLog(tmp_path, agent).shard.name
    assert "~" in name and len(name.encode()) <= 255


def test_events_in_a_shard_the_old_naming_wrote_are_still_read_and_ordered_before_new_ones(
    tmp_path,
):
    # r-replay-compat: `x:y` used to write x_y.jsonl; its new shard has a digest, and the old
    # file is still replayed (every shard is read) with the clock carried on past it
    old = EventLog(tmp_path, "x_y")
    for i in range(3):
        old.append("session.note", "s1", {"text": f"old{i}", "item": ""})
    new = EventLog(tmp_path, "x:y")
    new.append("session.note", "s1", {"text": "new0", "item": ""})
    assert new.shard.name != "x_y.jsonl"
    assert _notes(new) == ["old0", "old1", "old2", "new0"]


def test_an_id_that_is_not_valid_utf8_is_refused_not_a_crash_in_the_digest(tmp_path):
    # DDFLOW_AGENT bytes that are not UTF-8 arrive as lone surrogates (surrogateescape)
    with pytest.raises(ValueError, match="agent id"):
        _write(tmp_path, "bad\udcffid")
