"""`ddflow verify --all/--phase`: every done task checked, worst first (B-verify-sweep)."""

from __future__ import annotations

from conftest import run_cli
from helpers import git as _git

from ddflow.api.verify import verify_sweep
from ddflow.core import outcome as O


def _commit(repo, files, msg="work"):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _project(repo):
    """T-GOOD landed what it declared; T-FALSE declared a file that was never created;
    T-OPEN is not done. Gates are forced past, which every one of them shows."""
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "T-GOOD", "--phase", "P1", "--globs", "good.py,tests/test_good.py")
    good = _commit(repo, {"good.py": "1\n", "tests/test_good.py": "def test_g():\n    pass\n"})
    run_cli(repo, "complete", "T-GOOD", "--sha", good, "--force")
    run_cli(repo, "task", "add", "T-FALSE", "--phase", "P1", "--globs", "missing/promised.py")
    run_cli(repo, "complete", "T-FALSE", "--sha", good, "--force")
    run_cli(repo, "task", "add", "T-OPEN", "--phase", "P1", "--globs", "open.py")


def test_the_sweep_ranks_the_false_completion_first_and_skips_open_tasks(repo):
    _project(repo)
    out = verify_sweep(repo)
    assert out.exit == O.FAIL and out.data["checked"] == 2
    assert out.data["worst"][0]["item"] == "T-FALSE"
    first = out.data["worst"][0]
    assert first["verdict"] == "does not hold"
    assert any(p["id"] == "declared_files" and p["status"] == "fail" for p in first["problems"])
    assert "T-OPEN" not in {w["item"] for w in out.data["worst"]}
    assert out.data["counts"]["does not hold"] == 2  # both are forced past required gates


def test_the_listing_is_bounded_by_limit(repo):
    _project(repo)
    out = verify_sweep(repo, limit=1)
    assert out.data["shown"] == 1 and len(out.data["worst"]) == 1 and out.data["checked"] == 2


def test_a_phase_scopes_the_sweep_and_an_unknown_phase_is_refused(repo):
    _project(repo)
    run_cli(repo, "phase", "add", "P2", "--title", "other")
    run_cli(repo, "task", "add", "T-ELSE", "--phase", "P2", "--globs", "e.py")
    run_cli(repo, "complete", "T-ELSE", "--force")
    assert verify_sweep(repo, phase="P1").data["checked"] == 2
    assert verify_sweep(repo, phase="P2").data["checked"] == 1
    assert verify_sweep(repo, phase="NOPE").exit == O.FAIL


def test_file_bugs_files_one_per_failing_completion_and_a_rerun_files_nothing_twice(repo):
    _project(repo)
    first = verify_sweep(repo, file_bugs=True)
    assert "T-FALSE" in first.data["bugs_filed"]
    out = run_cli(repo, "bug", "list")[1]
    assert "verify: the completion of T-FALSE" in out
    n = out.count("verify: the completion of T-FALSE")
    again = verify_sweep(repo, file_bugs=True)
    assert "T-FALSE" not in again.data["bugs_filed"]
    assert run_cli(repo, "bug", "list")[1].count("verify: the completion of T-FALSE") == n


def test_nothing_done_is_nothing_to_verify(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert verify_sweep(repo).exit == O.NOTHING


def test_the_cli_sweeps_with_all_and_phase_and_rejects_sweep_flags_on_one_task(repo):
    import json

    _project(repo)
    code, out, _ = run_cli(repo, "verify", "--all")
    assert code == 1 and "T-FALSE" in out and "declared_files" in out and "checked 2" in out
    code, out, _ = run_cli(repo, "--json", "verify", "--phase", "P1", "--limit", "1")
    body = json.loads(out)
    assert code == 1 and body["checked"] == 2 and len(body["worst"]) == 1
    code, _, err = run_cli(repo, "verify", "T-FALSE", "--file-bugs")
    assert code == 1 and "sweep" in err
    code, _, err = run_cli(repo, "verify", "T-FALSE", "--limit", "1")
    assert code == 1 and "sweep" in err
    code, _, err = run_cli(repo, "verify")
    assert code == 1 and "verify what" in err
    assert run_cli(repo, "verify", "--phase", "NOPE")[0] == 1


def test_the_mcp_tool_sweeps_when_no_id_is_given(repo):
    import json

    from ddflow.surfaces.mcp import Server

    _project(repo)
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "ddflow_verify", "arguments": {"limit": 1}}}
    )  # fmt: skip
    text = reply["result"]["content"][0]["text"]
    body = json.loads(text[text.index("{") :])
    assert body["checked"] == 2 and body["worst"][0]["item"] == "T-FALSE"


def test_the_mcp_tool_refuses_sweep_arguments_given_with_an_id(repo):
    from ddflow.surfaces.mcp import Server

    _project(repo)
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "ddflow_verify", "arguments": {"id": "T-GOOD", "file_bugs": True}}}
    )  # fmt: skip
    text = reply["result"]["content"][0]["text"]
    assert "for a sweep" in text and "T-GOOD: " not in text


def test_default_valued_sweep_arguments_with_an_id_are_not_a_refusal(repo):
    from ddflow.surfaces.mcp import Server

    _project(repo)
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "ddflow_verify",
                    "arguments": {"id": "T-GOOD", "phase": "", "file_bugs": False}}}
    )  # fmt: skip
    text = reply["result"]["content"][0]["text"]
    assert "for a sweep" not in text and "T-GOOD" in text


def test_an_explicit_limit_with_an_id_is_refused_on_both_surfaces_whatever_its_value(repo):
    from ddflow.surfaces.mcp import Server

    _project(repo)
    code, _, err = run_cli(repo, "verify", "T-GOOD", "--limit", "20")
    # T-GOOD verifies cleanly, so exit 1 here can only be the refusal, and the message says so
    assert code == 1 and "sweep" in err and "T-GOOD: " not in err
    code, _, err = run_cli(repo, "verify", "T-GOOD", "--limit", "0")
    assert code == 1 and "sweep" in err
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "ddflow_verify", "arguments": {"id": "T-GOOD", "limit": 0}}}
    )  # fmt: skip
    assert "for a sweep" in reply["result"]["content"][0]["text"]


def test_an_unknown_phase_is_refused_before_its_descendants_are_looked_up(repo, monkeypatch):
    from ddflow.core.model import State

    def boom(self, item_id):
        raise AssertionError("descendants() was asked about an unvalidated phase")

    monkeypatch.setattr(State, "descendants", boom)
    _project(repo)
    assert verify_sweep(repo, phase="NOPE").exit == O.FAIL


def test_a_single_id_failure_over_mcp_keeps_the_item_verdict_claims_shape(repo):
    """B1ad5aa1408 (4): `payload` went from ("item", "verdict", "claims") to "" (the whole
    data) so the sweep can use it. A failing verification is a RESULT, not a refusal: the
    body stays exactly those three fields, is an error (exit 1), and has no `refusal` lead;
    a misused argument combination IS a refusal and leads with one."""
    import json

    from ddflow.surfaces.mcp import Server

    _project(repo)

    def call(args):
        reply = Server(repo).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "ddflow_verify", "arguments": args}}
        )  # fmt: skip
        return reply["result"], json.loads(reply["result"]["content"][0]["text"])

    result, body = call({"id": "T-FALSE"})
    assert result["isError"] is True and set(body) == {"schema", "item", "verdict", "claims"}
    assert body["item"] == "T-FALSE" and body["verdict"] == "does not hold"
    assert any(c["id"] == "declared_files" and c["status"] == "fail" for c in body["claims"])
    assert "refusal" not in body

    result, body = call({"id": "T-GOOD", "pack": True, "judge": True})
    assert next(iter(body)) == "refusal" and body["refusal"]["exit"] == 3
    assert "pack or judge" in body["refusal"]["reason"]
