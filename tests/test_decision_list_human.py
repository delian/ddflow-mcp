"""`ddflow decision list` without --json prints its rows (bug Bb177c2e0e9).

The human renderer read `row["live"]`, a property the API's plain() rows never carried,
so the command crashed with KeyError: 'live' while --json worked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _add(repo, *args):
    code, out = run_cli(repo, "decision", "add", *args)[:2]
    assert code == 0, out


def test_decision_list_prints_live_and_superseded_rows(repo):
    run_cli(repo, "init")
    _add(repo, "--id", "D1", "--title", "Old way", "--decision", "a", "--globs", "src/*")
    _add(
        repo,
        "--id",
        "D2",
        "--title",
        "New way",
        "--decision",
        "b",
        "--globs",
        "src/*",
        "--supersedes",
        "D1",
    )

    code, out = run_cli(repo, "decision", "list")[:2]
    assert code == 0, out
    assert "D2" in out and "New way" in out and "D1" not in out.split("superseded;")[0], out

    code, out = run_cli(repo, "decision", "list", "--all")[:2]
    assert code == 0, out
    assert "D1" in out and "superseded -> D2" in out, out


def test_json_rows_say_whether_each_decision_is_live(repo):
    run_cli(repo, "init")
    _add(repo, "--id", "D1", "--title", "Old way", "--decision", "a", "--globs", "src/*")
    _add(
        repo,
        "--id",
        "D2",
        "--title",
        "New way",
        "--decision",
        "b",
        "--globs",
        "src/*",
        "--supersedes",
        "D1",
    )
    rows = json.loads(run_cli(repo, "--json", "decision", "list", "--all")[1])
    assert {r["id"]: r["live"] for r in rows} == {"D1": False, "D2": True}
