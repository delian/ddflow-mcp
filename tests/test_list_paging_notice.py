"""Bug Bd515797884: a paged list was silent where it mattered. `--all` was documented as
"lists all rows already" but paged at 50; there was no server-side filter, so `| grep` on a
page found nothing and read as a miss; the cut notice was stdout only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import view_list, view_read
from ddflow.services import viewers as V


@pytest.fixture
def many(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T-needle", "--title", "Find Me")  # the OLDEST row
    for i in range(1, 8):
        run_cli(repo, "task", "add", f"T{i}", "--title", f"title {i}")
    monkeypatch.setattr(V, "DEFAULT_LIMIT", 3)
    return repo


def test_match_filters_on_id_and_title_before_paging(many):
    out = view_list(many, "task", match="needle", limit=1)
    assert [r["id"] for r in out.data["rows"]] == ["T-needle"] and out.data["total"] == 1
    out = view_list(many, "task", match="FIND me")
    assert [r["id"] for r in out.data["rows"]] == ["T-needle"]
    assert view_read(many, "task", match="needle").data["total"] == 1


def test_all_lifts_the_page_size_for_every_kind_and_phase_is_unpaged():
    import argparse

    from ddflow.api import surf_reporting as R
    from ddflow.surfaces.commands import viewers_lists as VL

    def ns(**kw):
        return argparse.Namespace(**{"limit": None, "all": False, **kw})

    for kind in ("task", "bug", "lesson", "research"):
        assert VL._limit(ns(), kind) == R.LIST_DEFAULT_LIMIT
        assert VL._limit(ns(all=True), kind) == R.LIST_MAX_LIMIT
        assert VL._limit(ns(all=True, limit=7), kind) == 7
    assert VL._limit(ns(), "phase") == R.LIST_MAX_LIMIT  # 199 phases: a page hid most of them


def test_cut_notice_also_goes_to_stderr_when_stdout_is_not_a_tty(many):
    _code, out, err = run_cli(many, "task", "list", "--limit", "2")  # stdout is captured
    assert "PARTIAL LIST" in out and "PARTIAL LIST" in err
    assert "--match" in err


def test_help_no_longer_claims_all_lists_everything_already():
    from ddflow.surfaces.commands import viewers_lists as VL

    assert "already" not in VL._ALL_HELP[""]


def test_phase_list_prints_every_phase_past_the_page_size(many, monkeypatch, capsys):
    from ddflow.api import surf_reporting as R
    from ddflow.surfaces.cli import main

    monkeypatch.setattr(R, "LIST_DEFAULT_LIMIT", 2)
    for i in range(5):
        run_cli(many, "phase", "add", f"P{i}", "--title", f"phase {i}")
    assert main(["--repo", str(many), "phase", "list"]) == 0
    out = capsys.readouterr().out
    assert all(f"P{i} " in out for i in range(5)) and "PARTIAL" not in out
    assert main(["--repo", str(many), "task", "list"]) == 0  # a task page is still cut
    assert "PARTIAL LIST" in capsys.readouterr().out


def test_notice_does_not_offer_all_once_at_the_most_a_list_gives():
    from ddflow.api import surf_reporting as R
    from ddflow.surfaces.commands.viewers_lists import cut_notice

    d = {"shown": 2, "total": 9, "offset": 0, "limit": 2, "next": 2}
    assert "--all" in cut_notice(d)
    d = {**d, "limit": R.LIST_MAX_LIMIT}
    assert "--all" not in cut_notice(d) and "--match" in cut_notice(d)


def test_mcp_phase_list_is_unpaged_like_the_cli():
    from ddflow.core import limits as L
    from ddflow.surfaces.tools.operations import _limit

    assert _limit({"kind": "phase"}) == L.LIST_MAX_LIMIT
    assert _limit({"kind": "task"}) == 25
    assert _limit({"kind": "task", "all": True}) == L.LIST_MAX_LIMIT
    assert _limit({"kind": "phase", "limit": 5}) == 5
