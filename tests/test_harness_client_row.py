"""SU-client-table-descriptors: `ddflow harness show <id>` says whether that agent honours
`tools/list_changed`, read from the client table (`mcpshim/clients.py`) by the descriptor's id.

The descriptors stay as they are (the strict loader and the pinned harness output do not change);
the join is by id, so there is one copy of the facts. R-su-descriptor-join.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.api.harness import harness_show
from ddflow.mcpshim import clients as C
from ddflow.services import harnessreg as HR

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def line(repo, which):
    msg = harness_show(repo, which).data["message"]
    return next((ln for ln in msg.splitlines() if "list_changed" in ln), "")


def test_show_prints_the_list_changed_row_of_an_agent_with_one(repo):
    got = line(repo, "claude")
    assert "honours" in got and "verified" in got and "2.1.296" in got


def test_show_says_ignores_for_an_agent_known_to_ignore_it(repo):
    assert "ignores" in line(repo, "qwen")


def test_show_says_unknown_for_an_agent_with_an_unknown_row(repo):
    assert "unknown" in line(repo, "antigravity")


def test_show_says_no_client_row_for_an_agent_without_one(repo):
    assert "no client row (unknown: treated as hard)" in line(repo, "glm")


def test_show_says_when_a_row_is_trusted(repo):
    assert "trusted from 2.1.296" in line(repo, "claude")
    assert "expires unless verified" in line(repo, "opencode")
    assert "by name" in line(repo, "qwen")


def test_by_harness_returns_the_main_row_of_an_agent():
    assert C.by_harness("claude").key == "claude-code"
    assert C.by_harness("cursor").key == "cursor-ide"  # the descriptor describes the IDE
    assert C.by_harness("no-such-harness") is None


def test_every_row_that_names_a_harness_names_a_real_descriptor():
    have = set(HR.ids())
    assert {e.harness for e in C.ENTRIES if e.harness} <= have


#: Descriptors with no client row yet. A new descriptor missing from both this list and the table
#: fails below, so a new agent is noticed; when someone probes one of these, delete it here.
NO_ROW = {"cody", "devin", "dsh", "glm", "qodo", "replit", "tabnine"}


def test_the_agents_without_a_row_are_exactly_the_listed_ones():
    without = set(HR.ids()) - {e.harness for e in C.ENTRIES if e.harness}
    assert without == NO_ROW, (sorted(without - NO_ROW), sorted(NO_ROW - without))
