"""Bc896ea5d16: the board listed only tasks under a phase, so a task added with no phase
(every B-fix-* task) never appeared, and with no phases at all it said 'No phases yet'.
They are an Unphased section, in text, markdown and --json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli


def _queue(repo: Path) -> None:
    run_cli(repo, "init")
    assert run_cli(repo, "phase", "add", "P1", "--title", "Phase one")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "phased")[0] == 0
    assert run_cli(repo, "task", "add", "LOOSE", "--title", "no phase at all")[0] == 0
    assert run_cli(repo, "task", "add", "LOOSE2", "--parent", "LOOSE", "--title", "kid")[0] == 0


def test_a_task_with_no_phase_is_on_the_board_in_text_and_json(repo):
    _queue(repo)
    text = run_cli(repo, "board")[1]
    assert "## Unphased" in text and "**LOOSE**" in text and "**LOOSE2**" in text, text
    assert text.index("**T1**") < text.index("## Unphased") < text.index("**LOOSE**")
    assert "0/2 tasks" in text.split("## Unphased")[1]
    data = json.loads(run_cli(repo, "--json", "board")[1])
    assert [t["id"] for t in data["unphased"]] == ["LOOSE", "LOOSE2"]
    assert data["unphased"][1]["depth"] == 1
    assert [t["id"] for p in data["phases"] for t in p["tasks"]] == ["T1"]
    # the markdown view `render` writes is the same document
    assert "**LOOSE**" in run_cli(repo, "render", "--show", "board")[1]


def test_a_board_with_no_phases_still_lists_unphased_tasks(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "LOOSE", "--title", "alone")[0] == 0
    text = run_cli(repo, "board")[1]
    assert "**LOOSE**" in text and "No phases yet" not in text, text


def test_a_phase_filter_shows_no_unphased_section(repo):
    _queue(repo)
    text = run_cli(repo, "board", "--phase", "P1")[1]
    assert "Unphased" not in text and "LOOSE" not in text, text
    assert json.loads(run_cli(repo, "--json", "board", "--phase", "P1")[1])["unphased"] == []
