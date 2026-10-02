"""The contract of auto ids (bug Bcc91b2328b): they are time-salted ON PURPOSE.

Identical text filed twice without an id gets two DIFFERENT ids, so two real reports
stay two records; the add-time duplicate check (D-no-duplicates) is what catches the
re-filing. Only an explicit id is stable. A comment claiming otherwise misleads the
reader into relying on a determinism that is not there.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from ddflow import api as A
from ddflow.core.ids import auto_id
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

ROOT = Path(__file__).resolve().parent.parent
TEXT = "claim refuses a worktree that already exists on disk instead of adopting it"


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def test_auto_id_differs_for_identical_input():
    ids = {auto_id("B", TEXT, "T1") for _ in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"B[0-9a-f]{10}", i) for i in ids)


def test_refiled_identical_bug_is_auto_linked_not_silently_merged(repo):
    first = A.bug_found(repo, summary=TEXT, agent="a")
    assert first.exit == 0
    second = A.bug_found(repo, summary=TEXT, agent="a")
    assert second.exit == 0
    # Not the same id (no silent merge into one record), but caught by the duplicate
    # check and linked to the first, automatically because the copy is exact.
    assert second.data["auto"] is True
    assert second.data["extended"] == first.data["id"]
    st = fold(EventLog(repo, "a").read_all(), strict=False)
    assert list(st.bugs) == [first.data["id"]]
    assert st.links[first.data["id"]].extensions[0]["text"] == TEXT


def test_an_explicit_id_is_what_makes_a_re_report_merge(repo):
    A.bug_found(repo, summary="one", id="B1", agent="a")
    again = A.bug_found(repo, summary="two", id="B1", agent="a")
    assert again.exit == 0 and again.data["id"] == "B1"


FALSE_CLAIMS = [
    (Path("ddflow/core/ids.py"), r"stable for identical content"),
    (Path("ddflow/core/ids.py"), r"content-addressed instead"),
    (Path("ddflow/api/knowledge.py"), r"same summary and item give the same auto id"),
]


@pytest.mark.parametrize("path,pattern", FALSE_CLAIMS, ids=str)
def test_no_source_claims_auto_ids_are_deterministic(path, pattern):
    assert not re.search(pattern, (ROOT / path).read_text(), re.I), (path, pattern)


def test_the_auto_id_docstring_says_it_is_time_salted():
    doc = auto_id.__doc__ or ""
    assert "time" in doc.lower() and "differ" in doc.lower()
