"""scripts/token_report.py: the interim transcript token report (TE-measure).

Fixture transcripts with known sums; aggregates only; unreadable input is UNAVAILABLE, never zero."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "token_report.py"
spec = importlib.util.spec_from_file_location("token_report", SCRIPT)
TR = importlib.util.module_from_spec(spec)
sys.modules["token_report"] = TR
spec.loader.exec_module(TR)

SECRET = "sk-SECRETSECRETSECRET"


def _ts(minute: int) -> str:
    return f"2026-10-23T10:{minute:02d}:00.000Z"


def _asst(mid, minute, *, tool=None, cmd=None, w5=0, w1=0, read=0, out=10, dup=False):
    content = [{"type": "text", "text": "hi"}]
    if tool:
        content.append(
            {"type": "tool_use", "id": "tu_" + mid, "name": tool, "input": {"command": cmd or ""}}
        )
    usage = {
        "input_tokens": 1,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": w5 + w1,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
        "output_tokens": 1 if dup else out,
    }
    return {
        "type": "assistant",
        "timestamp": _ts(minute),
        "message": {"id": mid, "content": content, "usage": usage},
    }


def _result(mid, minute, text):
    block = {"type": "tool_result", "tool_use_id": "tu_" + mid, "content": text}
    return {"type": "user", "timestamp": _ts(minute), "message": {"content": [block]}}


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


@pytest.fixture
def proj(tmp_path):
    lead = [
        _asst("m1", 0, tool="Bash", cmd="ls", w1=1000, read=0, out=100, dup=True),
        _asst("m1", 0, tool="Bash", cmd="ls", w1=1000, read=0, out=100),  # last streamed entry wins
        _result("m1", 1, "x" * 400),
        _asst("m2", 2, read=1000, out=10),
    ]
    _write(tmp_path / "lead.jsonl", lead)
    sub = tmp_path / "lead" / "subagents"
    sub.mkdir(parents=True)
    agent = [
        _asst("a1", 0, tool="Bash", cmd="sleep 20; echo " + SECRET, w5=100, out=0),
        _result("a1", 9, "ok"),
        _asst("a2", 10, read=100, w5=60000, out=0),  # gap 10 min after a poll: a rewrite
        _asst("a3", 11, read=60100, out=0),
        _asst(
            "a4", 12, tool="Bash", cmd="ddflow review x", read=60100, w5=70000, out=0
        ),  # gap 1 min: not a rewrite
        _asst("a5", 40, read=1, w5=80000, out=0),  # gap 28 min after review: a rewrite
    ]
    _write(sub / "agent-a.jsonl", agent)
    return tmp_path


def test_weights_and_split(proj):
    rep = TR.build_report(proj)
    lead, subs = rep["lead"], rep["subagents"]
    assert lead["calls"] == 2
    assert lead["read"] == 1000 and lead["write_1h"] == 1000 and lead["output"] == 110
    assert lead["units"] == pytest.approx(2 * 1 + 1000 * 0.1 + 1000 * 2 + 110 * 5)
    assert subs["agents"] == 1 and subs["calls"] == 5
    assert subs["write_5m"] == 100 + 60000 + 70000 + 80000
    assert 0.0 < rep["share"]["subagents"] < 1.0
    assert rep["share"]["lead"] + rep["share"]["subagents"] == pytest.approx(1.0)


def test_rewrites_by_preceding_tool(proj):
    rw = TR.build_report(proj)["rewrites"]
    assert rw["poll"]["events"] == 1 and rw["poll"]["tokens"] == 60000
    assert rw["review"]["events"] == 1 and rw["review"]["tokens"] == 80000
    assert sum(v["events"] for v in rw.values()) == 2
    assert rw["poll"]["avg_wait_min"] == 10.0 and rw["review"]["avg_wait_min"] == 28.0


def test_gap_and_size_are_knobs(proj):
    rw = TR.build_report(proj, gap_s=60 * 30)["rewrites"]
    assert sum(v["events"] for v in rw.values()) == 0
    rw = TR.build_report(proj, min_write=75000)["rewrites"]
    assert sum(v["events"] for v in rw.values()) == 1
    rep = TR.build_report(proj, weights={**TR.WEIGHTS, "output": 0})
    assert rep["lead"]["units"] == pytest.approx(2 + 100 + 2000)


def test_carry_share_classes(proj):
    carry = TR.build_report(proj)["carry"]
    assert carry["bash_result"]["added"] == 100  # 400 bytes / 4
    assert carry["bash_result"]["carry"] == 100 * 1  # one later call re-reads it
    assert sum(v["share_carry"] for v in carry.values()) == pytest.approx(1.0)


def test_window_filters_entries(proj):
    rep = TR.build_report(proj, since="2026-10-24")
    assert rep["lead"]["calls"] == 0 and rep["subagents"]["calls"] == 0


def test_no_transcript_text_in_output(proj):
    out = json.dumps(TR.build_report(proj)) + TR.render_text(TR.build_report(proj))
    assert SECRET not in out and "sleep 20" not in out and "hi" not in out.replace("units", "")


def test_unreadable_file_is_unavailable_not_zero(proj):
    bad = proj / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe not json at all\n{broken\n")
    rep = TR.build_report(proj)
    assert rep["lead"]["calls"] == 2  # the good file still counts
    assert rep["unavailable"] and rep["unavailable"][0].startswith("bad")
    os.chmod(proj / "lead.jsonl", 0)
    if os.access(proj / "lead.jsonl", os.R_OK):
        pytest.skip("running as a user that ignores file modes")
    rep = TR.build_report(proj)
    assert any(u.startswith("lead") for u in rep["unavailable"])
    assert rep["lead"]["status"] == "UNAVAILABLE"


def test_missing_dir_cli_exit_2(tmp_path):
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True
    )
    assert r.returncode == 2 and "UNAVAILABLE" in r.stdout


def test_cli_json(proj):
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(proj), "--json"], capture_output=True, text=True
    )
    assert r.returncode == 0
    assert json.loads(r.stdout)["schema"] == "token_report@1"
