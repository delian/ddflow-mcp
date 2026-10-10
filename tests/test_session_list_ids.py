"""Regression for B-session-list-short-id: the ids `session list` prints resolve in `session show`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

LONG_IDS = ["s20261008T152231-1734981", "s20261008T152231-1734982", "S-short"]


def _put(repo, agent, sid, n):
    d = repo / ".ddflow" / "events"
    d.mkdir(parents=True, exist_ok=True)
    ev = {
        "agent": agent,
        "data": {"model": "m"},
        "id": f"ev{n:020d}",
        "kind": "session.started",
        "lamport": n,
        "schema": 1,
        "subject": sid,
        "ts": f"2026-10-08T15:22:{n:02d}Z",
    }
    with (d / f"{agent}.jsonl").open("a") as f:
        f.write(json.dumps(ev, sort_keys=True) + "\n")


def test_every_listed_id_is_full_and_resolves_with_session_show(repo):
    run_cli(repo, "init")
    for n, sid in enumerate(LONG_IDS, start=1):
        _put(repo, "alice", sid, n)
    code, out, err = run_cli(repo, "session", "list")
    assert code == 0, err
    listed = [ln.split()[0] for ln in out.splitlines() if ln.startswith("  ")]
    assert sorted(listed) == sorted(LONG_IDS)
    for sid in listed:
        code, _out, err = run_cli(repo, "session", "show", sid)
        assert code == 0, f"{sid}: {err}"
