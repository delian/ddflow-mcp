"""B5fa04e7fe7: `bug found` accepted an empty (or whitespace-only) summary and filed a bug
with a blank title. A bug without a summary is a record nobody can act on, so an empty
summary is refused and nothing is written, exactly like an unknown scope or severity."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _bugs(repo):
    return fold(EventLog(repo, "a").read_all(), strict=False).bugs


def test_an_empty_summary_is_refused_and_writes_nothing(repo):
    run_cli(repo, "init")
    out = K.bug_found(repo, summary="", agent="a")
    assert not out.ok, out.data
    assert "summary" in out.reason, out.reason
    assert not _bugs(repo), "an empty summary must not file a bug"


def test_a_whitespace_only_summary_is_refused_too(repo):
    run_cli(repo, "init")
    out = K.bug_found(repo, summary="   \t  ", agent="a")
    assert not out.ok, out.data
    assert not _bugs(repo), "a whitespace-only summary must not file a bug"


def test_the_cli_refuses_an_empty_summary(repo):
    run_cli(repo, "init")
    code, _, err = run_cli(repo, "bug", "found", "--summary", "")
    assert code == 1 and "summary" in err, err
    assert not _bugs(repo)
