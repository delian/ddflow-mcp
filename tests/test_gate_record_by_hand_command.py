"""An agent cannot record a command gate passed by hand (bug Bed643a611b).

`gate record <id> unit_tests --outcome passed --command 'pytest -q' --exit-code 0` with no run
was accepted as `gate.passed`, and the report's by_hand heuristic read the `command` key as
proof that a tool produced it. "A tool that could not run is recorded unavailable, never
passed": a command gate's pass comes from `gate run`; by hand, an agent may record
`unavailable` or `partial`. The operator's override (`gate skip`, a person's terminal) stays.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import report_gates

OK, REFUSED = 0, 3
#: The configured command itself, typed by hand: the refusal is the missing run, not a mismatch.
CMD = f"{sys.executable} -c \"print('1 passed')\""
HAND = ("gate", "record", "T1", "unit_tests", "--outcome", "passed", "--command", CMD,
        "--exit-code", "0", "--evidence", "ran it")  # fmt: skip


def _setup(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps(CMD)}\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")


def _gate(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates.get("unit_tests")


def test_an_agent_cannot_hand_record_a_never_run_command_gate_passed(repo):
    _setup(repo)
    code, out, err = run_cli(repo, *HAND, agent="a1")
    assert code == REFUSED, out + err
    assert "gate run" in out + err and "unavailable" in out + err
    assert _gate(repo) is None


def test_the_ran_by_provenance_cannot_be_typed_by_a_caller(repo):
    _setup(repo)
    code, out, err = run_cli(repo, *HAND, "--ran-by", "ddflow ci run", agent="a1")
    assert code not in (OK, REFUSED) or _gate(repo) is None, out + err
    assert _gate(repo) is None


def test_an_unknown_ran_by_does_not_spare_a_hand_record():
    from ddflow.services.gates.handrecord import RAN_BY, hand_record

    assert RAN_BY == {"ddflow ci run"}
    gdef = SimpleNamespace(is_command_gate=True)
    it = SimpleNamespace(id="T1", gates={})
    ev = SimpleNamespace(ran_by="typed by an agent", command="x", output_file="")
    why, _ = hand_record(it, gdef, "ci", "passed", "--agent a", None, ev, "h", skip=False)
    assert "gate run" in why
    ev.ran_by = "ddflow ci run"
    assert hand_record(it, gdef, "ci", "passed", "--agent a", None, ev, "h", skip=False) == ("", {})


def test_the_mcp_gate_record_tool_has_no_ran_by_argument():
    from ddflow.surfaces.mcp import TOOLS

    props = TOOLS["ddflow_gate_record"]["properties"]
    assert "ran_by" not in props and "ran-by" not in props, sorted(props)


def test_an_agent_may_record_it_unavailable_or_after_a_real_run(repo):
    _setup(repo)
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "unavailable",
        "--reason", "no runner here", agent="a1",
    )  # fmt: skip
    assert code == OK, out + err
    assert run_cli(repo, "gate", "run", "T1", "unit_tests", agent="a1")[0] == OK
    assert _gate(repo).outcome == "passed"


def test_a_person_at_a_terminal_may_still_record_it(repo, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    _setup(repo)
    code, out, err = run_cli(repo, *HAND)
    assert code == OK, out + err


def test_the_report_counts_a_bare_command_string_as_by_hand():
    cfg = SimpleNamespace(session=SimpleNamespace(report_note_chars=200))
    hand = SimpleNamespace(outcome="passed", by="a", reason="", evidence={"command": "pytest -q"})
    ran = SimpleNamespace(
        outcome="passed", by="a", reason="", evidence={"command": "x", "command_digest": "abc"}
    )
    assert report_gates._row("unit_tests", hand, cfg)["by_hand"] is True
    assert report_gates._row("unit_tests", ran, cfg)["by_hand"] is False
    ci = SimpleNamespace(
        outcome="passed", by="a", reason="", evidence={"command": "x", "ran_by": "ddflow ci run"}
    )
    assert report_gates._row("ci", ci, cfg)["by_hand"] is False
