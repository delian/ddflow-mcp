"""DX-tests-cmd-not-full-suite: `ddflow tests` prints the selected-scope command; the whole-suite
command shows only behind --full, marked merge-queue / phase-end only."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_econ_tests import _fix, _project

FULL = "-m pytest -q tests\n"


def _text(repo, *args: str) -> str:
    return run_cli(repo, "tests", "--item", "fix-B1", *args)[1]


def test_the_full_suite_command_is_not_printed_by_default(repo):
    _project(repo)
    _fix(repo)
    text = _text(repo)
    assert FULL not in text + "\n" and "--full" in text
    assert "Run them now, in parallel" in text


def test_full_shows_the_command_marked_merge_queue_only(repo):
    _project(repo)
    _fix(repo)
    text = _text(repo, "--full")
    assert FULL in text and "MERGE QUEUE / PHASE END ONLY" in text
    assert text.index("Run them now") < text.index(FULL)  # the selected command comes first


def test_full_is_honoured_when_the_gate_runs_only_the_selection():
    from ddflow.surfaces.declared.export import _tests_text

    out = SimpleNamespace(
        exit=0,
        reason="",
        data={
            "tests": [],
            "changed": [],
            "base": "main",
            "command": "",
            "advice": "",
            "full_suite": "uv run pytest tests/",
            "full_suite_known": True,
            "unit_tests_gate": {"scope": "selected", "why": "w", "tests": [], "command": "x"},
        },
    )

    text = _tests_text(out, {})
    assert "MERGE QUEUE / PHASE END ONLY" in text and "uv run pytest tests/" in text
