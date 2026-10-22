"""A research item's deliverable can be a recorded note, not a commit (B-research-kind-item-
cannot-pass-implement-without-commit).

`gate record implement --outcome passed` refused every item whose branch had no commit ahead of
the base. A research-kind item that answers its question with a recorded research note
(`research add --item <id>`) has nothing to commit, so it could never pass `implement` without
--force. The note IS its implementation; a research item with no note, and any other kind with
no commit, are still refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC


def _item(repo: Path, kind: str) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Phase one")
    code, out, err = run_cli(
        repo, "task", "add", "T1", "--parent", "P1", "--title", "T1", "--kind", kind
    )
    assert code == 0, out + err
    assert LC.claim(repo, "T1", agent="worker").exit == 0


def _note(repo: Path) -> None:
    code, out, err = run_cli(
        repo, "research", "add", "--new", "--id", "R-T1", "--item", "T1", "--question", "why",
        "--claim", "because", "--mechanism", "m", "--falsifier", "f", "--probe", "p",
        "--probe-output", "o", "--verdict", "CONFIRMED",
    )  # fmt: skip
    assert code == 0, out + err


def _implement(repo: Path):
    return run_cli(
        repo, "gate", "record", "T1", "implement", "--outcome", "passed", "--evidence", "note R-T1"
    )


def test_a_research_item_with_a_recorded_note_passes_implement(repo):
    _item(repo, "research")
    _note(repo)
    code, out, err = _implement(repo)
    assert code == 0, out + err


def test_a_research_item_without_a_note_is_still_refused(repo):
    _item(repo, "research")
    code, out, err = _implement(repo)
    assert code != 0 and "no commit ahead" in out + err


def test_a_code_task_is_not_excused_by_a_note(repo):
    _item(repo, "task")
    _note(repo)
    code, out, err = _implement(repo)
    assert code != 0 and "no commit ahead" in out + err
