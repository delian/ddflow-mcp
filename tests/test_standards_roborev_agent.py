"""The standards gate records the agent roborev actually reviewed with (B03437a6b45).

Jobs enqueued as `agent: kilo` were reviewed by roborev's backup agent, claude-code, when
kilo failed (`roborev show N`: "(by claude-code)"), and `gate record standards --model
kilo` recorded kilo: a same-family reviewer passed as cross-family. With
`--reviewed-sha`, ddflow now reads roborev's job record for that commit and records the
agent that ran as the reviewer. Tested against a fake `roborev` on PATH.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _item(repo: Path) -> str:
    """T1 claimed, one commit in its worktree; returns the branch head."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1", agent="impl")
    assert code == 0, out + err
    st = fold(EventLog(repo).read_all(), strict=False)
    tree = (repo / st.items["T1"].worktree).resolve()
    (tree / "a.py").write_text("A = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    return _git(tree, "rev-parse", "HEAD")


@pytest.fixture
def fake_roborev(tmp_path, monkeypatch):
    """A `roborev` whose `list --json` prints the jobs written to ``jobs.json``."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    jobs = tmp_path / "jobs.json"
    script = bin_dir / "roborev"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:3] != ['list', '--json']:\n"
        "    sys.exit(2)\n"
        f"print(open({str(jobs)!r}).read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def write(rows: list[dict]) -> None:
        jobs.write_text(json.dumps(rows))

    write([])
    return write


def _record(repo: Path, head: str, model: str = "kilo"):
    return run_cli(
        repo, "gate", "record", "T1", "standards", "--outcome", "passed",
        "--evidence", "roborev: no findings", "--model", model,
        "--reviewed-sha", head, agent="impl",
    )  # fmt: skip


def _gate(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["standards"]


def _job(id_: int, ref: str, agent: str, model: str = "", status: str = "done") -> dict:
    return {"id": id_, "git_ref": ref, "agent": agent, "model": model, "status": status,
            "job_type": "review"}  # fmt: skip


def test_the_agent_that_ran_is_recorded_not_the_one_typed(repo, fake_roborev):
    head = _item(repo)
    fake_roborev([_job(7, head, "claude-code")])
    code, out, err = _record(repo, head)
    assert code == 0, out + err
    rec = _gate(repo)
    assert rec.evidence["model"] == "claude-code", rec.evidence
    assert rec.evidence["roborev"] == {"job": 7, "agent": "claude-code", "model": ""}
    assert "roborev job 7 was reviewed by claude-code" in out + err
    # ...so reviewer independence sees an anthropic reviewer, not an unknown 'kilo'.
    assert G.family_of(rec.evidence["model"], Config()) == "anthropic"


def test_a_range_review_ending_at_the_head_counts_and_the_newest_wins(repo, fake_roborev):
    head = _item(repo)
    fake_roborev([
        _job(7, head, "claude-code"),
        _job(9, f"abc123..{head}", "kilo", "elmdeepseek/deepseek-ai/DeepSeek-V4.1-Flash"),
        _job(11, head, "claude-code", status="running"),
    ])  # fmt: skip
    code, out, err = _record(repo, head)
    assert code == 0, out + err
    rec = _gate(repo)
    assert rec.evidence["model"] == "elmdeepseek/deepseek-ai/DeepSeek-V4.1-Flash"
    assert rec.evidence["roborev"]["job"] == 9
    assert "was reviewed by" not in out + err, "kilo ran as asked: nothing to say"


def test_no_review_of_the_sha_keeps_the_typed_model_with_a_note(repo, fake_roborev):
    head = _item(repo)
    fake_roborev([_job(7, "0" * 40, "claude-code")])
    code, out, err = _record(repo, head)
    assert code == 0, out + err
    assert _gate(repo).evidence["model"] == "kilo"
    assert "no finished review" in out + err


def test_without_roborev_the_typed_model_stands(repo, monkeypatch):
    head = _item(repo)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "standards", "--outcome", "passed",
        "--evidence", "linters", "--model", "kilo", "--reviewed-sha", head, agent="impl",
    )  # fmt: skip
    assert code == 0, out + err
    assert _gate(repo).evidence["model"] == "kilo"


def test_a_null_job_list_is_no_review_not_a_crash(repo, fake_roborev, tmp_path):
    """`roborev list --json` prints `null` for a repository it has no jobs for."""
    head = _item(repo)
    (tmp_path / "jobs.json").write_text("null")
    code, out, err = _record(repo, head)
    assert code == 0, out + err
    assert "no finished review" in out + err
