"""Regression for B-search-history-short-id: `search` and `history` print ids whole, so each
printed id resolves in `session show` (never shortened below what `show` accepts)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

SID = "s20261008T152231-1734981"


def _put(repo, kind, n, **data):
    d = repo / ".ddflow" / "events"
    d.mkdir(parents=True, exist_ok=True)
    ev = {
        "agent": "alice",
        "data": data,
        "id": f"ev{n:020d}",
        "kind": kind,
        "lamport": n,
        "schema": 1,
        "subject": SID,
        "ts": f"2026-10-08T15:22:{n:02d}Z",
    }
    with (d / "alice.jsonl").open("a") as f:
        f.write(json.dumps(ev, sort_keys=True) + "\n")


def _fixture(repo):
    run_cli(repo, "init")
    _put(repo, "session.started", 1, model="m")
    _put(repo, "session.prompt", 2, text="zebra crossing question")


def _resolves(repo, ident):
    code, _out, err = run_cli(repo, "session", "show", ident)
    assert code == 0, f"{ident}: {err}"


def test_search_prints_the_whole_session_id(repo):
    _fixture(repo)
    code, out, err = run_cli(repo, "search", "zebra")
    assert code == 0, err
    row = next(ln for ln in out.splitlines() if "zebra" in ln)
    assert SID in row
    _resolves(repo, next(t for t in row.split() if t.startswith("s2026")))


def test_history_prints_the_whole_session_id(repo):
    _fixture(repo)
    code, out, err = run_cli(repo, "history", "--limit", "50")
    assert code == 0, err
    ids = {t for ln in out.splitlines() for t in ln.split() if t.startswith("s2026")}
    assert ids == {SID}
    _resolves(repo, SID)
