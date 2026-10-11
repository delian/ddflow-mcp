"""A gate pass made with a user-local command is labelled, not mistaken for the project's (B924bdc92d7).

`[gate.unit_tests].command` can be set in a user's git-ignored `.ddflow/local/config.toml`.
Alice's pass with her own command used to read `[x] unit_tests` for everyone, with no word
that the command was hers. The evidence now says which layer the command came from
(`command_layer`), carries a digest of it (`command_digest`), and names the machine that
holds the output log (`output_log_host`); `gate status`, `show` and `complete` label such a pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git
from helpers import make_event as _ev

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import completion
from ddflow.services import gates as G

LABEL = "passed with a local-only command (not the committed one)"
AGENT = "worker"


def _setup(repo: Path, committed: str = "") -> None:
    run_cli(repo, "init")
    if committed:
        (repo / ".ddflow" / "gates.toml").write_text(
            f'[gate.unit_tests]\ncommand = "{committed}"\n'
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "gate config")
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == 0
    code, out, err = run_cli(repo, "claim", "T1", agent=AGENT)
    assert code == 0, out + err


def _local(repo: Path, command: str) -> None:
    code, out, err = run_cli(repo, "config", "--local", "--set", "gate.unit_tests.command", command)
    assert code == 0, out + err


def _run_gate(repo: Path) -> dict:
    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent=AGENT)
    assert code == 0, out + err
    return json.loads(out)["evidence"]


def _status(repo: Path) -> str:
    code, out, err = run_cli(repo, "gate", "status", "T1", agent=AGENT)
    assert code == 0, err
    return out


def test_a_local_command_pass_records_its_layer_digest_and_log_host(repo):
    _setup(repo)
    _local(repo, "python -c 'print(1)'")
    ev = _run_gate(repo)
    assert ev["command_layer"] == "local", ev
    assert ev["command_digest"] and ev["command_digest"] != ev["command"], ev
    assert ev["output_log_host"] == AGENT, ev


def test_gate_status_and_show_label_a_local_command_pass(repo):
    _setup(repo)
    _local(repo, "python -c 'print(1)'")
    _run_gate(repo)
    assert LABEL in _status(repo)
    code, out, err = run_cli(repo, "show", "T1", agent=AGENT)
    assert code == 0 and LABEL in out, out + err


def test_a_committed_command_pass_is_recorded_committed_and_not_labelled(repo):
    _setup(repo, committed="python -c 'print(2)'")
    ev = _run_gate(repo)
    assert ev["command_layer"] == "committed", ev
    assert "committed_command_digest" not in ev
    assert LABEL not in _status(repo)
    assert "local-only" not in _status(repo)


def test_when_the_committed_layer_has_a_different_command_the_label_says_so(repo):
    _setup(repo, committed="python -c 'print(2)'")
    _local(repo, "python -c 'print(1)'")
    ev = _run_gate(repo)
    assert ev["command_layer"] == "local" and ev["committed_command_digest"], ev
    assert ev["committed_command_digest"] != ev["command_digest"]
    text = _status(repo)
    assert LABEL in text and "committed command differs" in text, text


def test_a_local_pass_is_a_warning_when_the_item_completes(repo):
    _setup(repo)
    _local(repo, "python -c 'print(1)'")
    _run_gate(repo)
    cfg = Config.load(repo)
    v = completion.verdict(fold(EventLog(repo).read_all(), strict=False), cfg, "T1", repo=repo)
    assert any(LABEL in w for w in v.warnings), v.warnings


def test_evidence_recorded_before_the_keys_existed_is_not_labelled(repo):
    cfg = Config.load()
    st = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            _ev(
                "gate.passed", "T", 2, gate="unit_tests", evidence={"command": "pytest", "exit": 0}
            ),
        ]
    )
    assert LABEL not in G.status(st, cfg, "T").render()
    assert G.status(st, cfg, "T").local_command == {}
