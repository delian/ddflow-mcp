"""Bug Bbba7cecf08: `list --json` had no `rows` on a refusal, and `--all` was refused for
task, phase and research. Every list kind answers one JSON shape, with `rows` always."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

KINDS = ("task", "phase", "bug", "research", "lesson")


def _seed(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "phase one")
    for i in (1, 2, 3):
        run_cli(repo, "task", "add", f"T{i}", "--title", f"t{i}", "--phase", "P1")
    run_cli(repo, "task", "add", "T9", "--title", "elsewhere")


def _rows(repo, *argv):
    code, out, err = run_cli(repo, "--json", "list", *argv)
    return code, json.loads(out), err


def test_all_and_phase_return_rows_for_tasks(repo):
    _seed(repo)
    code, body, err = _rows(repo, "task", "--phase", "P1", "--limit", "100", "--all")
    assert code == 0, err
    assert sorted(r["id"] for r in body["rows"]) == ["T1", "T2", "T3"]
    assert body["total"] == 3


def test_text_and_json_row_counts_agree(repo):
    _seed(repo)
    for argv in (("task",), ("task", "--phase", "P1"), ("task", "--all"), ("phase", "--all")):
        code, text, _ = run_cli(repo, "list", *argv)
        jcode, body, _ = _rows(repo, *argv)
        assert code == jcode == 0
        ids = [ln.split()[0] for ln in text.splitlines() if ln.startswith("  ")]
        assert ids == [r["id"] for r in body["rows"]], argv


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_has_rows_even_when_refused_or_empty(repo, kind):
    _seed(repo)
    _code, body, _ = _rows(repo, kind, "--phase", "NOPE")
    assert body["rows"] == [] and body["total"] == 0 and body["record_kind"] == kind
    code, body, _ = _rows(repo, kind, "--all", "--state", "no-such-state")
    assert isinstance(body["rows"], list)


def test_a_stray_flag_refusal_has_the_same_keys_as_a_view_refusal(repo):
    _seed(repo)
    _c, stray, _ = _rows(repo, "task", "--item", "T1")
    _c, viewer, _ = _rows(repo, "task", "--phase", "NOPE")
    assert set(viewer) <= set(stray)


def test_research_add_refuses_all(repo):
    _seed(repo)
    code, _out, err = run_cli(repo, "research", "add", "--all")
    assert code != 0 and "--all" in err


def test_mcp_read_takes_all_for_tasks_and_refusals_keep_rows(repo):
    from ddflow.api import view_read

    _seed(repo)
    assert view_read(repo, "task", all=True).data["total"] == 4
    out = view_read(repo, "lesson", phase="NOPE")
    assert out.exit != 0 and out.data["rows"] == []
    assert view_read(repo, "bogus").data["rows"] == []


def test_engine_refusals_and_the_requested_limit_keep_the_shape(repo):
    from ddflow.api import view_read

    _seed(repo)
    assert view_read(repo, "search").data["rows"] == []
    assert view_read(repo, "session", state="nope-state").data["rows"] == []
    code, body, _ = _rows(repo, "task", "--item", "T1", "--limit", "5")
    assert code == 3 and "reason" in body and body["limit"] == 5
