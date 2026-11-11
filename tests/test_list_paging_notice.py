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
    for i in range(1, 8):
        run_cli(repo, "task", "add", f"T{i}", "--title", f"title {i}")
    run_cli(repo, "task", "add", "T-needle", "--title", "Find Me")
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
