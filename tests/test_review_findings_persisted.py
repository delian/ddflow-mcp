"""Bug B206: a review that outlives its caller must not lose its finding bodies.

B190's critic took 2055 s; the MCP client gave up at 1800 s with no progress
notification, and the only record of the findings was the lost tool response. Each
chunk's reply is now written to .ddflow/local/reviews/ as it arrives (digest in the
gate evidence), and the MCP server sends notifications/progress per line.
"""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp

BODY = "the claim: x is wrong because of y; probe: run z"


def _setup(repo: Path, tmp_path: Path) -> None:
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        f"printf 'FINDING HIGH x.py:1\\nSHORT\\n{BODY}\\n\\nSTATUS: FINDINGS 1\\n'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "a.py").write_text("x = 1\n")


def _gate(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]


def test_the_recorded_evidence_carries_the_finding_body_and_a_digest(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    ev = _gate(repo).evidence
    assert not Path(ev["output_file"]).is_absolute(), "the event log is committed"
    path = repo / ev["output_file"]
    assert path.is_relative_to(repo / ".ddflow" / "local" / "reviews")
    assert hashlib.sha256(path.read_bytes()).hexdigest()[:16] == ev["output_digest"]
    api.review(
        repo, gate="critic", item="T1", full=True
    )  # a second run does not overwrite the first
    assert hashlib.sha256(path.read_bytes()).hexdigest()[:16] == ev["output_digest"]
    assert repo / _gate(repo).evidence["output_file"] != path
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert BODY in rows[0]["reply"] and rows[0]["chunk"] == 1
    assert "fake" in path.name
    assert BODY in ev["chunk_findings"][0]["detail"]


def test_mcp_review_sends_progress_when_the_client_asks(repo, tmp_path):
    _setup(repo, tmp_path)
    srv = mcp.Server(repo, called_from=repo)
    sent: list[dict] = []
    first_seen: list[bool] = []

    def notify(frame: dict) -> None:
        if not sent:  # the gate is recorded only when the review ends
            rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates.get("critic")
            first_seen.append(bool(rec and rec.outcome))  # `gate.started` alone is no outcome
        sent.append(frame)

    srv.notify = notify
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_review",
                "arguments": {"id": "T1"},
                "_meta": {"progressToken": "tok"},
            },
        }
    )
    assert sent, "no notifications/progress was sent"
    assert first_seen == [False], "progress must stream while the review runs, not after"
    assert all(f["method"] == "notifications/progress" for f in sent)
    assert all(f["params"]["progressToken"] == "tok" for f in sent)
    assert [f["params"]["progress"] for f in sent] == list(range(1, len(sent) + 1))


def test_no_progress_token_means_no_notification(repo, tmp_path):
    _setup(repo, tmp_path)
    srv = mcp.Server(repo, called_from=repo)
    sent: list[dict] = []
    srv.notify = sent.append
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_review", "arguments": {"id": "T1"}},
        }
    )
    assert sent == []
