"""`ddflow history` as a log viewer: --agent (one shard), --tail, compact line, bounded --json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.history_verbs import HISTORY_VERBS


def _shard(repo: Path, agent: str, rows: list[dict]) -> None:
    d = repo / ".ddflow" / "events"
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i, r in enumerate(rows):
        out.append(
            {
                "id": f"e{agent}{i}",
                "ts": f"2026-09-26T10:0{i}:00",
                "lamport": r.get("lamport", 1000 + i),
                "agent": agent,
                "kind": r.get("kind", "item.blocked"),
                "subject": r.get("subject", "T9"),
                "data": r.get("data", {"reason": f"{agent} reason {i}"}),
            }
        )
    (d / f"{agent}.jsonl").write_text("\n".join(json.dumps(x) for x in out) + "\n")


def _two_agents(repo: Path) -> None:
    run_cli(repo, "init")
    _shard(repo, "alpha", [{} for _ in range(3)])
    _shard(repo, "beta", [{"lamport": 2000 + i} for i in range(3)])


def test_agent_narrows_to_one_shard(repo):
    _two_agents(repo)
    code, out, err = run_cli(repo, "--json", "history", "--agent", "alpha", "--limit", "100")
    assert code == 0, err
    evs = json.loads(out)["events"]
    assert evs and {e["agent"] for e in evs} == {"alpha"}


def test_tail_is_last_n_oldest_first(repo):
    _two_agents(repo)
    code, out, err = run_cli(repo, "--json", "history", "--agent", "alpha", "--tail", "2")
    assert code == 0, err
    evs = json.loads(out)["events"]
    assert [e["data"]["reason"] for e in evs] == ["alpha reason 1", "alpha reason 2"]


def test_compact_line_has_time_agent_kind_subject_summary(repo):
    _two_agents(repo)
    code, out, _ = run_cli(repo, "history", "--agent", "beta", "--tail", "1")
    assert code == 0
    line = next(ln for ln in out.splitlines() if "beta" in ln)
    assert line.split()[:2] == ["2026-09-26", "10:02"]
    assert "beta" in line and "T9" in line and HISTORY_VERBS["item.blocked"] in line
    assert "beta reason 2" in line


def test_json_truncates_large_payloads_with_a_note(repo):
    run_cli(repo, "init")
    _shard(repo, "alpha", [{"data": {"text": "x" * 5000}}])
    code, out, _ = run_cli(repo, "--json", "history", "--agent", "alpha")
    assert code == 0
    ev = json.loads(out)["events"][0]
    assert len(ev["data"]["text"]) < 1000
    assert "truncated" in ev["data"]["text"]
    assert ev.get("truncated") is True


def test_unknown_agent_is_nothing_not_everything(repo):
    _two_agents(repo)
    code, _out, _ = run_cli(repo, "history", "--agent", "nobody")
    assert code == 2


def test_tail_with_bad_value_is_refused(repo):
    _two_agents(repo)
    code, _out, _ = run_cli(repo, "history", "--tail", "0")
    assert code != 0
