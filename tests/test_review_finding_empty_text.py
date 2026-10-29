"""A review finding must carry text (B-review-finding-empty-text).

A reviewer that wrote only a location --

    FINDING MEDIUM y.py:1

with no paragraph -- was recorded with an empty `detail`, so `review show` and
`review triage` printed `#N [MEDIUM] y.py:1` with an empty body, and the finding
could not be refuted or confirmed with a probe: there was no text naming what an
author must answer.

The record path now derives a finding's text from its location when no paragraph
came back, and the finding stays a finding: it calls for a verdict, and the text a
triage records is the flagged location rather than nothing.
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
    (f,) = findings  # still ONE finding: the reviewer's FINDINGS 1 stays a finding
    assert f.location == "y.py:1"
    # The paragraph is missing, so the finding's text is derived from its location: it
    # names the spot the author must answer instead of recording an empty body.
    assert f.detail == "y.py:1"
    assert f.title == "y.py:1"


def test_a_block_that_names_nothing_is_kept_as_a_marked_finding_not_dropped():
    findings, on_contract = RV.parse("FINDING MEDIUM\n\nSTATUS: FINDINGS 1")
    assert on_contract
    (f,) = findings  # the reviewer declared a finding: it stays counted, not dropped
    assert f.location == ""
    assert f.title == "(unnamed finding)"
    assert f.detail == "(reviewer raised a finding but named nothing)"


def test_a_location_only_finding_is_recorded_with_its_location_as_text(repo, tmp_path):
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        "printf 'FINDING MEDIUM y.py:1\\n\\nSTATUS: FINDINGS 1\\n'\n"
    )
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
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    (recorded,) = rec.evidence["chunk_findings"]
    assert recorded["location"] == "y.py:1"
    assert recorded["detail"] == "y.py:1", "the finding carries its derived text"
    assert recorded["digest"], "the finding is still keyed, so a triage can address it"
    # The finding still counts, so the review still fails: deriving text may not turn a
    # location-only finding into a silent clean review.
    assert rec.outcome == "failed"
