"""B7372065218: `ddflow complete` had no read-only dry run, so an agent that only wanted to
read the unmet conditions had to run the real command -- and a standing policy then FORCED
the completion before the operator saw the request.

`complete --check` is that dry run: it writes nothing, never completes, and exits 0 when
nothing is unmet, 1 when conditions remain, 2 when it could not run (no such item).
"""

from __future__ import annotations

from pathlib import Path

from conftest import pass_pipeline, run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _add_task(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "note", "--globs", "docs/**")


def _events(repo: Path) -> list[bytes]:
    return sorted((repo / ".ddflow" / "events").glob("*.jsonl"))


def _state(repo: Path, item: str) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items[item].state


def test_complete_check_reports_unmet_conditions_and_writes_nothing(repo: Path) -> None:
    _add_task(repo)
    before = [p.read_bytes() for p in _events(repo)]
    code, out, err = run_cli(repo, "complete", "T1", "--check", agent="bot")
    assert code == 1, out + err
    # The read-only answer names the unmet conditions, not a completion.
    assert "unmet condition(s)" in out + err, out + err
    assert _state(repo, "T1") != "done"
    # Read-only: no event shard is written, so a check can never record a policy grant.
    assert [p.read_bytes() for p in _events(repo)] == before


def test_complete_check_exits_zero_when_nothing_is_unmet_and_does_not_complete(repo: Path) -> None:
    _add_task(repo)
    pass_pipeline(repo, "T1")
    code, out, err = run_cli(repo, "complete", "T1", "--check", "--model", "claude-opus-5", agent="bot")
    assert code == 0, out + err
    assert "may complete" in out + err, out + err
    # A check never completes: the item is still open afterwards.
    assert _state(repo, "T1") != "done"


def test_complete_check_could_not_run_on_a_missing_item(repo: Path) -> None:
    _add_task(repo)
    code, out, err = run_cli(repo, "complete", "NOPE", "--check", agent="bot")
    assert code == 2, out + err
    # It went through the check path, not argparse (which also exits 2 on the unknown flag).
    assert "no such item" in out + err, out + err
