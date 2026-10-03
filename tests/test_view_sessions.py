"""`ddflow session list` and `session show <id>`: read straight from the log.

Neither depends on `export sessions` being enabled. List: newest first, agent, span,
items, prompt count, open/ended, implicit marker. Show: every prompt and note in order,
redacted, an adopted orphan once. An unknown id is refused with its near matches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

SECRET = "sk-abcdefghijklmnop1234567890"


def _shard(repo: Path, agent: str) -> Path:
    d = repo / ".ddflow" / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{agent}.jsonl"


_n = [0]


def _put(repo, agent, kind, subject, ts, **data):
    _n[0] += 1
    ev = {
        "agent": agent,
        "data": data,
        "id": f"ev{_n[0]:020d}",
        "kind": kind,
        "lamport": _n[0],
        "schema": 1,
        "subject": subject,
        "ts": ts,
    }
    with _shard(repo, agent).open("a") as f:
        f.write(json.dumps(ev, sort_keys=True) + "\n")


def _fixture(repo):
    run_cli(repo, "init")
    _put(repo, "alice", "session.started", "S-old", "2026-09-01T10:00:00Z", model="m1")
    _put(
        repo,
        "alice",
        "session.prompt",
        "S-old",
        "2026-09-01T10:01:00Z",
        text="first ask",
        item="T1",
    )
    _put(
        repo, "alice", "session.note", "S-old", "2026-09-01T10:02:00Z", text="a dead end", item="T2"
    )
    _put(repo, "alice", "session.ended", "S-old", "2026-09-01T11:00:00Z", summary="done it")
    _put(repo, "bob", "session.started", "S-imp", "2026-10-02T09:00:00Z", model="m2", implicit=True)
    _put(repo, "bob", "session.prompt", "S-imp", "2026-10-02T09:05:00Z", text=f"key {SECRET}")
    # an orphan (no session id) and the copy adopt-orphans wrote under S-imp
    _put(repo, "bob", "session.prompt", "", "2026-10-02T09:10:00Z", text="orphan words")
    orphan_id = f"ev{_n[0]:020d}"
    _put(
        repo,
        "bob",
        "session.prompt",
        "S-imp",
        "2026-10-02T09:20:00Z",
        text="orphan words",
        adopted_from=orphan_id,
        orphan_at="2026-10-02T09:10:00Z",
    )


def test_list_newest_first_with_span_items_counts_and_markers(repo):
    _fixture(repo)
    code, out, err = run_cli(repo, "--json", "session", "list")
    assert code == 0, err
    body = json.loads(out)
    rows = body["rows"]
    assert body["total"] == 2 and body["truncated"] is False and body["record_kind"] == "session"
    assert [r["id"] for r in rows] == ["S-imp", "S-old"]
    imp, old = rows
    assert imp["implicit"] is True and old["implicit"] is False
    assert imp["state"] == "open" and old["state"] == "ended"
    assert imp["agent"] == "bob" and old["agent"] == "alice"
    assert imp["prompts"] == 2  # the adopted orphan counts once
    assert old["prompts"] == 1 and old["notes"] == 1
    assert old["items"] == 2
    assert old["started"] == "2026-09-01T10:00:00Z" and old["ended"] == "2026-09-01T11:00:00Z"
    assert old["last"] == "2026-09-01T11:00:00Z"


def test_list_human_marks_implicit_and_open(repo):
    _fixture(repo)
    code, out, _ = run_cli(repo, "session", "list")
    assert code == 0
    line = next(ln for ln in out.splitlines() if "S-imp" in ln)
    assert "implicit" in line and "open" in line
    assert "ended" in next(ln for ln in out.splitlines() if "S-old" in ln)
    assert SECRET not in out


def test_list_filters_since_limit_agent_state(repo):
    _fixture(repo)

    def ids(*a):
        return [
            r["id"] for r in json.loads(run_cli(repo, "--json", "session", "list", *a)[1])["rows"]
        ]

    assert ids("--limit", "1") == ["S-imp"]
    one = json.loads(run_cli(repo, "--json", "session", "list", "--limit", "1")[1])
    assert one["total"] == 2 and one["shown"] == 1 and one["truncated"] is True
    assert ids("--since", "2026-10-01") == ["S-imp"]
    assert ids("--owner", "alice") == ["S-old"]
    assert ids("--state", "ended") == ["S-old"]
    assert ids("--state", "open") == ["S-imp"]
    code, _out, err = run_cli(repo, "session", "list", "--since", "garbage")
    assert code == 3 and "since" in err
    code, _out, err = run_cli(repo, "session", "list", "--limit", "0")
    assert code == 3


def test_global_agent_flag_is_the_caller_not_a_filter(repo):
    _fixture(repo)
    code, out, _ = run_cli(repo, "--json", "session", "list", agent="someone-else")
    assert code == 0 and len(json.loads(out)["rows"]) == 2


def test_list_with_nothing_to_show_is_exit_2(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "session", "list")
    assert code == 2 and "No sessions" in out


def test_show_lists_prompts_and_notes_in_order_redacted_orphan_once(repo):
    _fixture(repo)
    code, out, err = run_cli(repo, "session", "show", "S-imp")
    assert code == 0, err
    assert SECRET not in out
    assert out.count("orphan words") == 1
    assert "implicit" in out
    code, out, _ = run_cli(repo, "session", "show", "S-old")
    assert code == 0
    assert out.index("first ask") < out.index("a dead end") < out.index("done it")
    assert "T1" in out and "T2" in out


def test_show_json_has_the_whole_record(repo):
    _fixture(repo)
    code, out, _ = run_cli(repo, "--json", "session", "show", "S-old")
    assert code == 0
    d = json.loads(out)
    assert d["id"] == "S-old" and d["summary"] == "done it" and d["state"] == "ended"
    assert [(e["kind"], e["text"]) for e in d["entries"]] == [
        ("prompt", "first ask"),
        ("note", "a dead end"),
    ]
    assert d["entries"][0]["item"] == "T1"
    assert d["items"] == ["T1", "T2"]


def test_show_unknown_id_refused_with_near_matches(repo):
    _fixture(repo)
    code, _out, err = run_cli(repo, "session", "show", "S-ol")
    assert code == 3
    assert "no session 'S-ol'" in err and "S-old" in err


def test_works_with_export_sessions_disabled(repo):
    _fixture(repo)
    # Nothing enabled the sessions export: no export event, no generated document.
    kinds = {
        json.loads(ln)["kind"]
        for shard in (repo / ".ddflow" / "events").glob("*.jsonl")
        for ln in shard.read_text().splitlines()
    }
    assert not any(k.startswith("export.") for k in kinds)
    assert not list(repo.rglob("SESSIONS.md"))
    code, out, _ = run_cli(repo, "session", "show", "S-old")
    assert code == 0 and "first ask" in out


def test_unattached_orphans_are_not_a_session(repo):
    run_cli(repo, "init")
    _put(repo, "bob", "session.prompt", "", "2026-10-02T09:10:00Z", text="lost words")
    code, out, _ = run_cli(repo, "session", "list")
    assert code == 2
    assert "adopt-orphans" in out


def test_an_event_with_null_data_does_not_break_the_viewer(repo):
    _fixture(repo)
    with _shard(repo, "alice").open("a") as f:
        f.write(
            json.dumps(
                {
                    "agent": "alice",
                    "data": None,
                    "id": "evnull",
                    "kind": "lease.released",
                    "lamport": 9999,
                    "schema": 1,
                    "subject": "T1",
                    "ts": "2026-10-02T12:00:00Z",
                }
            )
            + "\n"
        )
    code, out, err = run_cli(repo, "--json", "session", "list")
    assert code == 0, err
    assert len(json.loads(out)["rows"]) == 2
