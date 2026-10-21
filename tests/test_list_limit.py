"""Bug B-list-limit-silently-clamped (and Bf4c3b12455): a `--limit` above the list cap was cut
to the cap without saying so, the cut list told the caller to "raise --limit" (already above),
`--limit 0` was refused (MCP reads 0 as "the most"), and there was no way to walk the rest."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import view_list
from ddflow.services import viewers as V
from ddflow.surfaces.commands import viewers_lists as VL


@pytest.fixture
def seeded(repo, monkeypatch):
    run_cli(repo, "init")
    for i in range(1, 8):
        run_cli(repo, "task", "add", f"T{i}", "--title", f"t{i}")
    monkeypatch.setattr(V, "MAX_LIMIT", 3)
    return repo


def _ids(out):
    return [r["id"] for r in out.data["rows"]]


def test_limit_above_the_cap_says_it_was_clamped(seeded):
    out = view_list(seeded, "task", limit=50)
    assert out.data["limit"] == 3 and len(out.data["rows"]) == 3
    assert out.data["clamped_from"] == 50
    assert out.data["total"] == 7 and out.data["truncated"] is True


def test_limit_within_the_cap_is_not_flagged(seeded):
    out = view_list(seeded, "task", limit=2)
    assert out.data["clamped_from"] is None and out.data["next"] == 2


def test_limit_zero_is_the_most_a_list_gives(seeded):
    out = view_list(seeded, "task", limit=0)
    assert out.exit == 0 and out.data["limit"] == 3 and len(out.data["rows"]) == 3


def test_offset_walks_the_rest_and_next_ends(seeded):
    seen, offset = [], 0
    for _ in range(5):
        out = view_list(seeded, "task", limit=0, offset=offset)
        seen += _ids(out)
        offset = out.data["next"]
        if offset is None:
            break
    assert sorted(seen) == [f"T{i}" for i in range(1, 8)] and len(seen) == 7
    assert out.data["truncated"] is False


def test_offset_past_the_end_is_refused_with_the_total(seeded):
    out = view_list(seeded, "task", offset=99)
    assert out.exit != 0 and "7" in out.reason and out.data["rows"] == []


def test_text_footer_names_the_cap_and_the_next_offset(seeded):
    out = view_list(seeded, "task", limit=50)
    buf = io.StringIO()
    with redirect_stdout(buf):
        VL._print_rows("task", out, False)
    text = buf.getvalue()
    assert "raise --limit" not in text
    assert "cut to 3" in text and "--offset 3" in text
