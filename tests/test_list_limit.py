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
    assert "cut to 3" in text and "--limit 3 --offset 3" in text


@pytest.fixture
def cli(capsys):
    """The CLI in this process, so the cap patched on the engine applies to it."""
    from ddflow.surfaces.cli import main

    def run(repo, *argv):
        try:
            code = main(["--repo", str(repo), *argv])
        except SystemExit as exc:
            code = int(exc.code or 0)
        got = capsys.readouterr()
        return code, got.out, got.err

    return run


def test_cli_limit_above_the_cap_footer_is_honest(seeded, cli):
    code, out, _ = cli(seeded, "list", "task", "--limit", "5000")
    assert code == 0
    assert "raise --limit" not in out
    assert "--limit 5000 cut to 3" in out and "--limit 3 --offset 3" in out


def test_cli_limit_zero_lists_and_offset_pages(seeded, cli):
    import json

    code, out, _ = cli(seeded, "--json", "list", "task", "--limit", "0")
    first = json.loads(out)
    assert code == 0 and first["next"] == 3 and first["truncated"] is True
    code, out, _ = cli(seeded, "--json", "list", "task", "--limit", "0", "--offset", "3")
    second = json.loads(out)
    assert code == 0 and second["offset"] == 3 and second["next"] == 6
    assert not {r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]}


def test_offset_on_a_viewer_that_cannot_page_is_refused(seeded):
    from ddflow.api import view_read

    for kind in ("session", "search"):
        out = view_read(seeded, kind, query="t", offset=5)
        assert out.exit == 3 and "offset" in out.reason, kind


def test_mcp_note_pages_a_record_list_and_never_says_offset_none():
    from ddflow.surfaces.mcp_bound import bound_list

    rec = {"truncated": True, "rows": [1], "shown": 1, "total": 5, "next": 1}
    assert "limit=7 offset=1" in bound_list({**rec, "limit": 7}, {})[1]
    search = {"truncated": True, "rows": [1], "shown": 1, "total": 5}
    note = bound_list(search, {})[1]
    assert "None" not in note and "raise limit" in note


def test_research_add_refuses_a_list_only_offset(seeded, cli):
    code, _out, err = cli(seeded, "research", "--offset", "3")
    assert code != 0 and "--offset" in err


def test_mcp_note_does_not_say_raise_limit_at_the_maximum_and_reads_the_constants():
    """B-list-cap-hardcoded-and-search-note: a search already at its 200 maximum was told to
    'raise limit'; the maxima in the note are the constants, not copies of them."""
    from ddflow.api import surf_reporting as R
    from ddflow.surfaces.mcp_bound import bound_list

    at_max = {"truncated": True, "rows": [1], "shown": 1, "total": 999, "record_kind": "search"}
    note = bound_list({**at_max, "limit": R.SEARCH_MAX_LIMIT}, {})[1]
    assert "raise limit" not in note and "narrow the filters" in note
    below = bound_list({**at_max, "limit": 20}, {})[1]
    assert "raise limit" in below
    assert f"{R.LIST_MAX_LIMIT}, a search {R.SEARCH_MAX_LIMIT}" in below


def test_a_refused_list_echoes_the_clamped_limit_not_the_requested_one():
    from ddflow.api import surf_reporting as R
    from ddflow.api.viewers import empty_list

    assert empty_list("task", 5000)["limit"] == R.LIST_MAX_LIMIT
    assert empty_list("search", 5000)["limit"] == R.SEARCH_MAX_LIMIT
    assert empty_list("task", 0)["limit"] == R.LIST_MAX_LIMIT
    assert empty_list("task", 7)["limit"] == 7
