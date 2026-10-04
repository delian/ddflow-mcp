"""`ddflow verify <id> --reopen`: a false completion returns to the queue; a missed one is named (B-verify-reopen)."""

from __future__ import annotations

import json
import subprocess

from conftest import run_cli

from ddflow.api.verify import verify
from ddflow.core import outcome as O
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, files, msg):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _false_completion(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    run_cli(repo, "task", "add", "T1", "--title", "do it", "--globs", "missing/promised.py")
    sha = _commit(repo, {"other.txt": "x\n"}, "unrelated")
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")


def _item(repo, tid="T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[tid]


def test_a_failing_completion_is_reopened_with_the_failed_claims_and_its_gates_cleared(repo):
    _false_completion(repo)
    assert _item(repo).state == "done"
    out = verify(repo, "T1", reopen=True)
    assert out.exit == O.OK and out.data["reopened"] is True
    it = _item(repo)
    assert it.state == "open" and it.gates == {} and it.completed_at == ""
    assert it.reopened[0]["claims"] and "declared_files" in it.reopened[0]["reason"]
    assert it.reopened[0]["forced"] is False


def test_the_brief_puts_the_failed_claims_at_the_top_of_the_reopened_task(repo):
    _false_completion(repo)
    verify(repo, "T1", reopen=True)
    out = run_cli(repo, "brief", "--item", "T1")[1]
    _head, _, rest = out.partition("## Current: T1")
    current = rest.split("\n## ", 1)[0]  # the section about this task, not a later one
    assert "REOPENED by verification" in current
    assert "declared_files" in current and "gates were cleared" in current


def test_a_completion_that_holds_is_not_reopened_without_force_and_a_reason(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    sha = _commit(repo, {"w.py": "1\n"}, "merge T1: add w")
    for g in (
        "research",
        "rules",
        "implement",
        "rubber_duck",
        "critic",
        "standards",
        "unit_tests",
        "bug_hunt",
        "dedupe",
        "merge",
    ):
        EventLog(repo).append("gate.passed", "T1", {"gate": g})
    from ddflow.services import ledger as LG

    EventLog(repo).append(
        "item.completed", "T1",
        {"sha": sha, "kind": "task", "forced": False, "overridden": [], "ledger": LG.git_facts(repo, sha, _item(repo))},
    )  # fmt: skip
    assert verify(repo, "T1", reopen=True).exit == O.REFUSED
    assert _item(repo).state == "done"
    assert verify(repo, "T1", reopen=True, force=True).exit == O.REFUSED  # force needs a reason
    out = verify(repo, "T1", reopen=True, force=True, reason="the operator doubts it")
    assert out.exit == O.OK and _item(repo).reopened[0]["forced"] is True


def test_reopen_of_an_item_that_is_not_done_is_nothing_to_do(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert verify(repo, "T1", reopen=True).exit == O.NOTHING


def test_an_open_task_whose_work_landed_is_named_so_it_can_be_completed_not_redone(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    sha = _commit(repo, {"w.py": "1\n"}, "merge T1: add w")
    out = verify(repo, "T1")
    assert out.exit == O.NOTHING and out.data["appears_landed"]["sha"] == sha
    assert "ddflow complete T1" in out.reason and "--force" in out.reason


def test_the_cli_reopens_and_rejects_reopen_flags_without_an_id(repo):
    _false_completion(repo)
    code, out, _ = run_cli(repo, "--json", "verify", "T1", "--reopen")
    body = json.loads(out)
    assert code == 0 and body["reopened"] is True
    assert run_cli(repo, "verify", "--all", "--reopen")[0] == 1
    assert run_cli(repo, "verify", "T1", "--reopen")[0] == 2  # open now: nothing to reopen


def test_a_reopened_item_has_no_ledger_until_it_is_completed_again(repo):
    from ddflow.services import ledger as LG

    _false_completion(repo)
    assert LG.build(EventLog(repo).read_all(), "T1") is not None
    verify(repo, "T1", reopen=True)
    assert LG.build(EventLog(repo).read_all(), "T1") is None
    run_cli(repo, "complete", "T1", "--force")
    again = LG.build(EventLog(repo).read_all(), "T1")
    assert again is not None and again["forced"] is True


def test_reopening_one_task_does_not_hide_another_tasks_completion(repo):
    from ddflow.services import ledger as LG

    _false_completion(repo)  # T1 done
    run_cli(repo, "task", "add", "T2", "--title", "other", "--globs", "elsewhere.py")
    run_cli(repo, "complete", "T2", "--force")
    verify(repo, "T2", reopen=True)  # a later reopen of T2
    assert _item(repo, "T2").state == "open"
    assert LG.build(EventLog(repo).read_all(), "T1") is not None
    assert verify(repo, "T1").data["verdict"] == "does not hold"


def test_the_mcp_tool_reopens_and_refuses_reopen_arguments_without_an_id(repo):
    from ddflow.surfaces.mcp import Server

    def call(args):
        reply = Server(repo).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "ddflow_verify", "arguments": args}}
        )  # fmt: skip
        return reply["result"]["content"][0]["text"]

    _false_completion(repo)
    assert "need an id" in call({"reopen": True})
    assert '"reopened":true' in call({"id": "T1", "reopen": True})
    assert _item(repo).state == "open"


def test_the_mcp_tool_packs_and_refuses_mixed_modes(repo):
    from ddflow.surfaces.mcp import Server

    def call(args):
        reply = Server(repo).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "ddflow_verify", "arguments": args}}
        )  # fmt: skip
        return reply["result"]["content"][0]["text"]

    _false_completion(repo)
    assert "# Verify the completion of T1" in call({"id": "T1", "pack": True})
    assert "one of them" in call({"id": "T1", "pack": True, "judge": True})
    assert "one of them" in call({"id": "T1", "pack": True, "reopen": True})
    assert "for a sweep" in call({"id": "T1", "limit": 3})
    assert "need an id" in call({"judge": True})
