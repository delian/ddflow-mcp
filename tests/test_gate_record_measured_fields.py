"""A hand-recorded gate says which measured fields it lacks (bug Bb305b43f56).

`gate record --command ... --output-file P` stored P (a path on the recording agent's host)
and recorded no tree_sha, elapsed_s or command_digest -- silently, so `complete` could not
tell "not measured" from "lost". The record now names the agent whose host holds the file
(`output_file_host`, as a run's `output_log_host`) and lists what is absent (`unmeasured`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"].evidence


def _setup(repo: Path, monkeypatch, *claim: str) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps("true")}\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    if claim:
        assert run_cli(repo, "claim", "T1", *claim)[0] == OK


def _hand(repo: Path, tmp_path: Path):
    log = tmp_path / "run.log"
    log.write_text("1 passed in 0.1s\n")
    return run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "passed",
        "--command", "pytest -q", "--exit-code", "0", "--output-file", str(log),
    )  # fmt: skip


def test_a_hand_record_names_the_host_of_its_output_file_and_what_it_did_not_measure(
    repo, tmp_path, monkeypatch
):
    _setup(repo, monkeypatch)
    code, out, err = _hand(repo, tmp_path)
    assert code == OK, out + err
    ev = _ev(repo)
    assert ev["output_file_host"], ev
    assert ev["tree_sha"] and "tree_sha" not in ev.get("unmeasured", []), ev
    assert set(ev["unmeasured"]) >= {"elapsed_s", "command_digest"}, ev


def test_a_hand_record_with_no_tree_to_measure_says_tree_sha_is_absent(repo, tmp_path, monkeypatch):
    _setup(repo, monkeypatch, "--no-worktree")
    code, out, err = _hand(repo, tmp_path)
    assert code == OK, out + err
    ev = _ev(repo)
    assert "tree_sha" not in ev
    assert "tree_sha" in ev["unmeasured"], ev
