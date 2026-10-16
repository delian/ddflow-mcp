"""`ddflow review show <item> --gate G` lists the recorded round's numbered findings.

The findings used to print only in the running `ddflow review` (lost if stdout was not
kept); to read finding N an agent dug `chunk_findings` out of an event-log line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_triage import _full_review, _setup, _triage

from ddflow import api

OK, FAIL = 0, 1


def test_show_lists_the_numbered_findings_with_digest_and_triage_state(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _triage(repo, 1, "refuted", "ran it: no such bug")
    out = api.review_show(repo, "T1", gate="critic")
    assert out.exit == OK, out.reason
    rows = out.data["findings"]["critic"]
    assert [r["n"] for r in rows] == [1, 2]
    assert [r["triage"] for r in rows] == ["refuted", "untriaged"]
    assert all(len(r["digest"]) == 16 for r in rows)
    assert rows[0]["severity"] == "HIGH" and rows[0]["location"].startswith("a.py")
    assert rows[0]["probe"] == "ran it: no such bug"
    assert "#1 [HIGH]" in out.data["text"] and "UNTRIAGED" in out.data["text"]
    assert "1 refuted, 0 confirmed, 1 untriaged" in out.data["text"]


def test_the_numbers_shown_are_the_ones_triage_takes(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    shown = api.review_show(repo, "T1", gate="critic").data["findings"]["critic"]
    _triage(repo, shown[1]["n"], "confirmed", "fixed")
    after = api.review_show(repo, "T1", gate="critic").data["findings"]["critic"]
    assert [r["triage"] for r in after] == ["untriaged", "confirmed"]


def test_the_cli_verb_and_json(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    code, out, err = run_cli(repo, "review", "show", "T1", "--gate", "critic")
    assert code == OK, err
    assert "#1 [HIGH]" in out and "#2 [LOW]" in out, out
    code, out, err = run_cli(repo, "--json", "review", "show", "T1")
    assert code == OK, err
    assert len(json.loads(out)["findings"]["critic"]) == 2


def test_an_item_without_findings_is_refused_with_the_reason(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review_show(repo, "T1", gate="critic")
    assert out.exit == FAIL and "no `ddflow review` with numbered findings" in out.reason
    assert api.review_show(repo, "NOPE").exit == FAIL


def test_the_mcp_triage_tool_without_a_finding_lists_them(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    _setup(repo, tmp_path)
    _full_review(repo)
    call = {"name": "ddflow_review_triage", "arguments": {"id": "T1", "gate": "critic"}}
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call})
    assert not reply["result"].get("isError"), reply
    assert "#1 [HIGH]" in str(reply) and "#2 [LOW]" in str(reply), reply


def test_show_does_not_swallow_triage_flags(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    code, _out, err = run_cli(repo, "review", "show", "T1", "--finding", "1", "--refuted")
    assert code != OK and "triage" in err, err


def test_a_round_whose_blob_is_gone_is_named_not_called_missing(repo, tmp_path, monkeypatch):
    from ddflow.api import review_findings as RF
    from ddflow.core import blobref as B

    _setup(repo, tmp_path)
    _full_review(repo)
    gone = B.Unavailable("abc123", "was pruned")
    real = RF.LB.full

    def full(ev):
        got = dict(real(ev))
        if got.get("chunk_findings"):
            got["chunk_findings"] = gone
        return got

    monkeypatch.setattr(RF.LB, "full", full)
    out = api.review_show(repo, "T1")
    assert out.exit == FAIL and "blob must be restored" in out.reason, out.reason


def test_triage_tool_with_a_finding_but_no_probe_is_still_refused(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    _setup(repo, tmp_path)
    _full_review(repo)
    call = {"name": "ddflow_review_triage", "arguments": {"id": "T1", "finding": 1}}
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call})
    assert reply["result"].get("isError") and "probe" in str(reply), reply
