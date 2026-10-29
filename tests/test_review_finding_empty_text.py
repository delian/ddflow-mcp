"""A review finding must carry text (B-review-finding-empty-text).

A reviewer that wrote only a location --

    FINDING MEDIUM y.py:1

with no paragraph -- was recorded with an empty `detail`, so `review show` and
`review triage` printed `#N [MEDIUM] y.py:1` with an empty body, and the finding
could not be refuted or confirmed with a probe: there was no claim to answer.

The record path now derives a finding's text from its location when no paragraph
came back, so every recorded finding has text a triage can act on.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import review as RV

LOCATION_ONLY = "FINDING MEDIUM y.py:1\n\nSTATUS: FINDINGS 1"


def test_parse_derives_text_for_a_finding_with_location_only():
    findings, on_contract = RV.parse(LOCATION_ONLY)
    assert on_contract
    (f,) = findings
    assert f.location == "y.py:1"
    assert f.title, "a location-only finding must still carry a title"
    assert f.detail, "a location-only finding must still carry text to triage"


def test_a_location_only_finding_is_recorded_with_text(repo, tmp_path):
    cli = tmp_path / "fake-reviewer"
    cli.write_text("#!/bin/sh\ncat >/dev/null\nprintf 'FINDING MEDIUM y.py:1\\n\\nSTATUS: FINDINGS 1\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "a.py").write_text("x = 1\n")
    api.review(repo, gate="critic", item="T1")
    run = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    (recorded,) = run["chunk_findings"]
    assert recorded["detail"].strip(), "the recorded finding carries its text"
