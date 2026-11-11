"""DX-command-log: the git-ignored `.ddflow/local/cmdlog.jsonl` ring log of the commands a
session ran. `cmdlog.record` appends (argv shape + exit code + output tail), `cmdlog.recent`
reads the last N, and `reprobundle` reports them instead of "keeps no log of the commands"."""

from __future__ import annotations

import argparse
from pathlib import Path

from ddflow.core.redact import redact_argv
from ddflow.services import cmdlog
from ddflow.surfaces import cli


def _record(tmp_path: Path, argv, code=2, tail="", agent="me", **kw) -> None:
    cmdlog.record(tmp_path, agent, list(argv), code, tail, **kw)


def test_a_record_round_trips_argv_exit_and_tail(tmp_path: Path):
    _record(
        tmp_path,
        ["ddflow", "claim", "foo", "--note", "sekrit"],
        2,
        "blocked",
        verbs={"claim"},
        flags={"--note"},
    )
    (row,) = cmdlog.recent(tmp_path, 10)
    assert row["exit"] == 2
    assert row["argv"] == ["ddflow", "claim", "<arg>", "--note", "<value>"], row["argv"]
    assert row["agent"] == "me"
    assert "blocked" in row["tail"] and "sekrit" not in row["tail"]


def test_a_value_typed_after_a_kept_flag_keeps_only_its_shape(tmp_path: Path):
    assert redact_argv(["ddflow", "merge", "x"], ("merge",), ()) == ["ddflow", "merge", "<arg>"]
    assert redact_argv(["ddflow", "--agent", "f4"], (), ("--agent",)) == [
        "ddflow",
        "--agent",
        "<value>",
    ]


def test_an_empty_log_reads_as_nothing(tmp_path: Path):
    assert cmdlog.recent(tmp_path, 10) == []


def test_asking_for_zero_records_is_not_the_whole_ring(tmp_path: Path):
    _record(tmp_path, ["ddflow"], 0)
    assert cmdlog.recent(tmp_path, 0) == []  # n=0 is none, never every record


def test_recent_is_bounded_to_n(tmp_path: Path):
    for i in range(5):
        _record(tmp_path, ["ddflow"], i)
    assert [r["exit"] for r in cmdlog.recent(tmp_path, 3)] == [2, 3, 4]


def test_the_log_drops_the_oldest_past_the_cap(tmp_path: Path):
    for i in range(cmdlog.CAP_ENTRIES + 3):
        _record(tmp_path, ["ddflow"], i + 1)  # distinct exits, none zero
    rows = cmdlog.recent(tmp_path, cmdlog.CAP_ENTRIES + 3)
    assert rows[0]["exit"] == 4, "the oldest 3 records were not dropped past the cap"
    assert (tmp_path / cmdlog.REL).read_text().count("\n") <= cmdlog.CAP_ENTRIES


def test_the_command_log_is_not_written_when_there_is_no_repo(tmp_path: Path, monkeypatch):
    """The command log lives under a repo root; run outside a git repository there is none, so
    the best-effort log must not fall back to the cwd and leave a stray `.ddflow` (Bd7c147dfb9)."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.delenv("DDFLOW_REPO", raising=False)
    args = argparse.Namespace(repo=None, agent="me")
    cli._remember_command(args, ["ddflow", "onboard", "status"], 1, "", set(), set())
    assert not (plain / ".ddflow").exists(), "a non-git directory must not gain a .ddflow dir"


def test_the_command_log_still_falls_back_to_a_repository_cwd(repo: Path, monkeypatch):
    """A target that is not a repository does not mean `skip`: when the process runs from
    inside a real repository, the command belongs to that repository's log."""
    plain = repo.parent / "outside"
    plain.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.delenv("DDFLOW_REPO", raising=False)
    args = argparse.Namespace(repo=str(plain), agent="me")  # a non-repository target
    cli._remember_command(args, ["ddflow", "onboard", "status"], 1, "", set(), set())
    assert (repo / ".ddflow" / "local" / "cmdlog.jsonl").exists()


def test_the_tail_keeps_the_command_but_not_the_machine_home(tmp_path: Path):
    home = str(Path.home())
    _record(tmp_path, ["ddflow"], 0, f"boom {home}/secret")
    (row,) = cmdlog.recent(tmp_path, 10)
    assert home not in row["tail"], row["tail"]
    assert "boom" in row["tail"]
