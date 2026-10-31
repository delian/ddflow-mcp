"""DX-approve-help-doc-gaps: `ddflow approve --help` says who may run it, what it approves and
how to see pending requests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _help(repo) -> str:
    code, out = run_cli(repo, "approve", "--help")[:2]
    assert code == 0
    return " ".join(out.split())


def test_help_says_it_is_person_only(repo):
    text = _help(repo)
    assert "PERSON" in text and "refused" in text and "no MCP tool" in text


def test_help_says_what_is_approved_and_digest_bound(repo):
    text = _help(repo)
    assert "baseline-red" in text and "partial" in text and "digest" in text


def test_help_says_how_to_see_pending_requests(repo):
    text = _help(repo)
    assert "ddflow approvals list" in text and "--approval request" in text
