"""Onboarding stage 6: every probe, including the ones that must refuse.

A hook that exists but accepts a forbidden trailer is the failure the probe is for; a
handshake against the REGISTERED entry is the only proof the MCP server really starts.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import legacy as L
from ddflow.services import onboard_verify as V


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _hook(repo: Path, name: str, body: str) -> Path:
    path = _write(repo, f".git/hooks/{name}", "#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


FAKE_SERVER = """
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake"}}}), flush=True)
    elif msg.get("method") == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "x"}]}}), flush=True)
"""


def _check(report: V.VerifyReport, name: str) -> V.Check:
    found = [c for c in report.checks if c.name == name]
    assert len(found) == 1, report.render()
    return found[0]


def test_a_registered_entry_is_really_started_and_answers(repo, tmp_path):
    server = _write(tmp_path, "fake_server.py", FAKE_SERVER)
    _write(
        repo,
        ".mcp.json",
        json.dumps({"mcpServers": {"ddflow": {"command": sys.executable, "args": [str(server)]}}}),
    )
    report = V.verify(repo, suite=False)
    check = _check(report, "mcp handshake")
    assert check.outcome == "passed" and "fake" in check.detail and "1 tools" in check.detail


def test_no_registered_entry_is_unavailable_not_a_pass(repo):
    report = V.verify(repo, suite=False)
    assert _check(report, "mcp handshake").outcome == "unavailable"


def test_hooks_must_be_executable_and_carry_the_invocation(repo):
    report = V.verify(repo, suite=False)
    assert _check(report, "hooks armed").outcome == "failed"
    _hook(repo, "pre-commit", "# ddflow invocation\n")
    _hook(repo, "commit-msg", "# ddflow invocation\n")
    report = V.verify(repo, suite=False)
    assert _check(report, "hooks armed").outcome == "passed"


def test_the_trailer_probe_requires_a_refusal(repo):
    _hook(repo, "commit-msg", "exit 0\n")
    assert _check(V.verify(repo, suite=False), "trailer refused").outcome == "failed"
    _hook(repo, "commit-msg", 'grep -q Co-Authored-By "$1" && exit 1\nexit 0\n')
    assert _check(V.verify(repo, suite=False), "trailer refused").outcome == "passed"


def test_the_freeze_ratchet_must_exist_and_hold(repo):
    # No import here, so no ratchet is EXPECTED: unavailable, not failed.
    assert _check(V.verify(repo, suite=False), "freeze ratchet").outcome == "unavailable"
    _write(repo, "todo.md", "the old backlog\n")
    L.write_manifest(repo, ["todo.md"])
    report = V.verify(repo, suite=False)
    assert _check(report, "freeze ratchet").outcome == "passed"
    (repo / "todo.md").write_text("edited\n")
    assert _check(V.verify(repo, suite=False), "freeze ratchet").outcome == "failed"


def test_brief_must_print_something(repo):
    check = _check(V.verify(repo, suite=False), "brief/next answer")
    assert check.outcome == "passed" and "line(s)" in check.detail


def test_the_suite_check_uses_the_given_command_in_a_detached_tree(repo):
    report = V.verify(repo, suite_command="sh -c 'echo 2 passed in 0.1s'")
    check = _check(report, "suite green")
    assert check.outcome == "passed" and "detached tree" in check.detail
    report = V.verify(repo, suite_command="sh -c 'echo 1 passed, 1 failed; exit 1'")
    assert _check(report, "suite green").outcome == "failed"


def test_without_a_command_the_suite_check_is_unavailable(repo):
    assert _check(V.verify(repo), "suite green").outcome == "unavailable"


def test_a_hook_that_fails_everything_is_not_enforcement(repo):
    """Exit 127 for every message used to read as 'refused the trailer' (roborev)."""
    _hook(repo, "commit-msg", "exit 127\n")
    check = _check(V.verify(repo, suite=False), "trailer refused")
    assert check.outcome == "failed" and "clean message" in check.detail


def test_the_configured_suite_command_is_found_and_run(repo):
    """The lookup went through Config.gate, which does not exist; gates live in
    .ddflow/gates.toml and load_gates is the workflow's reader (roborev)."""
    _write(
        repo,
        ".ddflow/gates.toml",
        "[gate.unit_tests]\ncommand = \"sh -c 'echo 1 passed in 0.1s'\"\n",
    )
    check = _check(V.verify(repo), "suite green")
    assert check.outcome == "passed", check.detail


def test_an_import_without_a_ratchet_is_failed_not_hidden(repo, monkeypatch):
    """The unfrozen branch must fire when imports exist (roborev on c61278a4)."""
    monkeypatch.setattr(V.L, "imported_files", lambda state: ["todo.md"])
    check = _check(V.verify(repo, suite=False), "freeze ratchet")
    assert check.outcome == "failed" and "unfrozen" in check.detail


def test_the_report_says_what_did_not_pass(repo):
    report = V.verify(repo, suite=False)
    assert not report.passed
    rendered = report.render()
    assert "did not pass" in rendered and "hooks armed" in rendered
