"""`review triage` without --gate (bug Bca71987363).

Finding numbers are per gate. An omitted --gate used to default to `critic`, so a verdict
meant for a rubber_duck finding landed on critic's finding of the same number.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.infra.log import EventLog

OK, FAIL = 0, 1


def _setup(repo: Path, tmp_path: Path, gates=("critic", "rubber_duck")) -> None:
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        'printf "FINDING HIGH a.py:1\\nsomething wrong\\n\\n"\n'
        'echo "STATUS: FINDINGS 1"\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    names = ", ".join(f'"{g}"' for g in gates)
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        f'model = "gemini-2.5-pro"\ngates = [{names}]\nhedge = 1\n'
    )
    run_cli(repo, "task", "add", "T1", "--title", "add file", "--globs", "*.py")
    (repo / "a.py").write_text("x = 1\n")
    for g in gates:
        assert api.review(repo, gate=g, item="T1").data["outcome"] == "failed"


def _triaged(repo: Path) -> list:
    return [e for e in EventLog(repo).read_all() if e.kind == "review.triaged"]


def _cli(repo, *extra):
    return run_cli(
        repo, "review", "triage", "T1", "--finding", "1", "--refuted", "--probe", "ran it", *extra
    )


def test_omitted_gate_is_refused_when_two_gates_have_findings(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = _cli(repo)
    assert code == FAIL and "critic" in err and "rubber_duck" in err and "--gate" in err, err
    assert _triaged(repo) == []
    out = api.triage(repo, "T1", finding=1, verdict="refuted", probe="ran it")
    assert out.exit == FAIL and "rubber_duck" in out.reason and "critic" in out.reason
    assert _triaged(repo) == []


def test_omitted_gate_resolves_the_only_gate_with_findings_and_says_which(repo, tmp_path):
    _setup(repo, tmp_path, gates=("rubber_duck",))
    code, out, err = _cli(repo)
    assert code == OK, err
    assert "rubber_duck" in out
    events = _triaged(repo)
    assert len(events) == 1 and events[0].data["gate"] == "rubber_duck"


def test_explicit_gate_is_unchanged(repo, tmp_path):
    _setup(repo, tmp_path)
    code, _out, err = _cli(repo, "--gate", "rubber_duck")
    assert code == OK, err
    assert [e.data["gate"] for e in _triaged(repo)] == ["rubber_duck"]


def test_mcp_omitted_gate_is_refused_with_two_gates(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    _setup(repo, tmp_path)
    args = {"id": "T1", "finding": 1, "verdict": "refuted", "probe": "ran it"}
    call = {"name": "ddflow_review_triage", "arguments": args}
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call})
    assert reply["result"].get("isError") and "per gate" in str(reply), reply
    assert _triaged(repo) == []


def test_a_gate_with_undigested_findings_still_counts_as_having_findings(repo, tmp_path):
    """Review round 1: filtering on the digest let the one digested gate win silently."""
    from ddflow.core.model import fold

    _setup(repo, tmp_path)
    log = EventLog(repo)
    st = fold(log.read_all(), strict=False).items["T1"]
    ev = dict(st.gates["rubber_duck"].evidence)
    ev["chunk_findings"] = [{"severity": "LOW", "title": "old"}]  # no digest
    log.append("gate.failed", "T1", {"gate": "rubber_duck", "evidence": ev})
    out = api.triage(repo, "T1", finding=1, verdict="refuted", probe="ran it")
    assert out.exit == FAIL and "critic" in out.reason and "rubber_duck" in out.reason
    assert _triaged(repo) == []
