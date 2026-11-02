"""`ddflow session prompt` can mark a prompt as relayed by a coordinator.

Bug B-session-prompt-no-relay-attribution: an operator answer reaches a subagent through
a coordinator, but the session prompt had no field for relayed-by, so the log could not
distinguish the operator's own words from a relay. The prompt record now carries
``relayed_by`` (empty means the operator's own words).
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_cli

from ddflow.infra.log import EventLog


def _prompts(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo, "reader").read_all() if e.kind == "session.prompt"]


def _session(repo: Path) -> str:
    assert run_cli(repo, "init")[0] == 0
    code, out, _err = run_cli(repo, "session", "start")
    assert code == 0
    return out.strip().splitlines()[-1]


def test_a_prompt_records_the_coordinator_that_relayed_it(repo):
    sid = _session(repo)
    code, out, _err = run_cli(
        repo, "session", "prompt", sid, "--text", "the words", "--relayed-by", "coordinator-1"
    )
    assert code == 0, out + _err
    assert _prompts(repo)[0]["relayed_by"] == "coordinator-1", _prompts(repo)[0]


def test_a_direct_prompt_tells_a_relay_from_the_operators_own_words(repo):
    sid = _session(repo)
    assert run_cli(repo, "session", "prompt", sid, "--text", "do the thing")[0] == 0
    assert _prompts(repo)[0]["relayed_by"] == ""
