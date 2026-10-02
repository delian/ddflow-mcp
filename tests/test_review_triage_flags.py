"""Triage flags without the `triage` verb are refused, never run as a review (bug B3531d304ec).

`ddflow review <id> --gate G --finding N --refuted --probe ...` used to be accepted by the
plain `review` parser and ignored, starting a full reviewer run (25+ minutes on a shared
reviewer). It must refuse before any reviewer is contacted, and name the real command.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A command reviewer that leaves a marker file when (and only when) it is invoked."""
    marker = tmp_path / "reviewer-ran"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(f"#!/bin/sh\ncat >/dev/null\ntouch '{marker}'\necho 'STATUS: NO FINDINGS'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add a file", "--globs", "*.py")
    (repo / "a.py").write_text("x = 1\n")
    return marker


@pytest.mark.parametrize(
    "flags",
    [
        ["--finding", "3", "--refuted", "--probe", "p"],
        ["--finding", "1", "--confirmed", "--probe", "p"],
        ["--refuted"],
        ["--probe", "p"],
        ["--finding", "2"],
        ["--finding", "0"],  # given but falsy: still a triage flag
        ["--probe", ""],
    ],
)
def test_triage_flags_without_the_verb_are_refused_before_any_reviewer_runs(
    repo: Path, tmp_path: Path, flags: list[str]
) -> None:
    marker = _setup(repo, tmp_path)
    code, _out, err = run_cli(repo, "review", "T1", "--gate", "critic", *flags)
    assert code == 1, err
    assert "ddflow review triage T1 --gate critic" in err
    assert "--refuted|--confirmed" in err
    assert not marker.exists(), "a reviewer was contacted"


def test_triage_verb_with_chunk_is_refused(repo: Path, tmp_path: Path) -> None:
    marker = _setup(repo, tmp_path)
    code, _out, err = run_cli(
        repo, "review", "triage", "T1", "--gate", "critic", "--chunk", "1", "--finding", "1"
    )
    assert code == 1, err
    assert "--chunk" in err
    assert not marker.exists()


def test_plain_review_still_reaches_the_reviewer(repo: Path, tmp_path: Path) -> None:
    marker = _setup(repo, tmp_path)
    run_cli(repo, "review", "T1", "--gate", "critic")
    assert marker.exists()


def test_help_says_the_verb_is_required() -> None:
    import subprocess

    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "review", "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert "ddflow review triage <id>" in p.stdout
    assert "REQUIRES the\n`triage` verb" in p.stdout
