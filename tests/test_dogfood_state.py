"""B7: ddflow develops itself through ddflow, and the docs say so.

B7 stayed open for days after both halves had shipped, and three documents kept saying
"evaluated, not done" / "needs operator approval". This pins the facts and the prose.
"""

import json
from pathlib import Path

from tests.conftest import run_cli

ROOT = Path(__file__).resolve().parent.parent


def test_this_repository_runs_its_own_queue():
    assert "ddflow:begin rules/work-queue" in (ROOT / "AGENTS.md").read_text()
    assert "ddflow:begin rules/work-queue" in (ROOT / "CLAUDE.md").read_text()
    assert (ROOT / ".ddflow" / "config.toml").is_file()
    assert (ROOT / "docs" / "ddflow" / "QUEUE.md").is_file()
    shards = list((ROOT / ".ddflow" / "events").glob("*.jsonl"))
    assert shards, "the event log is committed with the code"
    code, out, err = run_cli(ROOT, "--json", "status")
    assert code == 0, out + err
    # a real queue: hundreds of items, not a toy
    assert json.loads(out)["tasks"]["total"] >= 100, out


def test_the_docs_no_longer_say_dogfooding_is_undone():
    backlog = (ROOT / "docs" / "BACKLOG.md").read_text()
    assert backlog.count("\n- **B7 ") == 1
    start = backlog.index("\n- **B7 ")
    end = backlog.find("\n- **", start + 1)
    entry = backlog[start : end if end != -1 else None]
    assert "CLOSED" in entry.split("\n\n")[0], entry
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text()
    assert "needs operator approval" not in handoff
    assert "no `AGENTS.md` or `CLAUDE.md`" not in handoff
    assert "an operator decision" not in handoff
    assert "since there is no `CLAUDE.md`" not in handoff
    assert "superseded: DONE 2026-09-28" in handoff
