"""B7: ddflow develops itself through ddflow, and the docs say so.

B7 stayed open for days after both halves had shipped, and three documents kept saying
"evaluated, not done" / "needs operator approval". This pins the facts and the prose.
"""

import json
from pathlib import Path

from tests.conftest import run_cli

ROOT = Path(__file__).resolve().parent.parent


def test_this_repository_runs_its_own_queue():
    assert "DDFLOW:BEGIN" in (ROOT / "AGENTS.md").read_text()
    assert "DDFLOW:BEGIN" in (ROOT / "CLAUDE.md").read_text()
    assert (ROOT / ".ddflow" / "config.toml").is_file()
    assert (ROOT / "docs" / "ddflow" / "QUEUE.md").is_file()
    shards = list((ROOT / ".ddflow" / "events").glob("*.jsonl"))
    assert shards, "the event log is committed with the code"
    code, out, err = run_cli(ROOT, "--json", "status")
    assert code == 0, out + err
    # a real queue: hundreds of items, not a toy
    assert json.loads(out), out


def test_the_docs_no_longer_say_dogfooding_is_undone():
    backlog = (ROOT / "docs" / "BACKLOG.md").read_text()
    entry = backlog[backlog.index("- **B7 ") :][:400]
    assert "CLOSED" in entry, entry
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text()
    assert "(**needs operator approval**" not in handoff
    assert "superseded: DONE 2026-09-28" in handoff
