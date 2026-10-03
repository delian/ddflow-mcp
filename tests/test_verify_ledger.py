"""The completion ledger (B-verify-ledger): what was required, what landed, what changed after."""

from __future__ import annotations

import json
import subprocess

from conftest import run_cli

from ddflow.infra.log import EventLog
from ddflow.services import ledger as LG


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _land(repo, tid="T1", extra=()):
    """Complete T1 the way `merge` + `complete` do, with a real commit behind the sha."""
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        tid,
        "--title",
        "add widget",
        "--body",
        "make a widget",
        "--globs",
        "w.py,tests/test_w.py",
    )
    (repo / "w.py").write_text("x = 1\n")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_w.py").write_text("def test_w():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "widget")
    sha = _git(repo, "rev-parse", "HEAD")
    code, out, err = run_cli(repo, "complete", tid, "--sha", sha, "--force", *extra)
    assert code == 0, out + err
    return sha


def test_complete_records_files_tests_and_the_requirement_digest(repo):
    sha = _land(repo)
    led = LG.build(EventLog(repo).read_all(), "T1")
    assert led["sha"] == sha and not led["reconstructed"]
    assert led["done"]["files_known"] and "w.py" in led["done"]["files"]
    assert led["done"]["tests"] == ["tests/test_w.py"]
    assert led["requirement"]["title"] == "add widget"
    assert led["forced"] is True  # --force was used: the ledger says so


def test_show_carries_the_ledger_for_a_done_item_and_not_for_an_open_one(repo):
    _land(repo)
    run_cli(repo, "task", "add", "T2", "--title", "later", "--globs", "z.py")
    _code, out, _ = run_cli(repo, "--json", "show", "T1")
    led = json.loads(out)["ledger"]
    assert led["files_total"] >= 2 and led["tests"] == 1
    assert "ledger" not in json.loads(run_cli(repo, "--json", "show", "T2")[1])
    assert "Completion ledger" in run_cli(repo, "show", "T1")[1]


def test_an_edit_after_completion_is_an_amendment_and_flags_the_requirement(repo):
    _land(repo)
    run_cli(repo, "update", "T1", "--body", "now it means something else")
    led = LG.build(EventLog(repo).read_all(), "T1")
    assert len(led["amendments"]) == 1 and "body" in led["amendments"][0]["fields"]
    assert led["requirement_changed_after"] is True
    # the requirement as it stood at completion is untouched
    assert led["requirement"]["body_chars"] == len("make a widget")


def test_a_skipped_gate_and_its_reason_are_in_the_ledger(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    run_cli(repo, "gate", "skip", "T1", "docs", "--reason", "internal only")
    run_cli(repo, "complete", "T1", "--force")
    led = LG.build(EventLog(repo).read_all(), "T1")
    assert led["skipped"] == ["docs"] and led["gates"]["docs"]["reason"] == "internal only"


def test_a_completion_without_a_sha_records_that_files_are_unknown(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    run_cli(repo, "complete", "T1", "--force")
    led = LG.build(EventLog(repo).read_all(), "T1")
    assert led["done"]["files_known"] is False and led["done"]["files"] == []


def test_a_completion_from_before_the_ledger_is_reconstructed_and_says_so(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    log = EventLog(repo)
    log.append(
        "item.completed", "T1", {"sha": "", "kind": "task", "forced": False, "overridden": []}
    )
    led = LG.build(log.read_all(), "T1")
    assert led["reconstructed"] is True


def test_the_requirement_digest_ignores_glob_order_but_not_the_text():
    a = LG.requirement_digest("t", "b", ["x", "y"])
    assert a == LG.requirement_digest("t", "b", ["y", "x"])
    assert a != LG.requirement_digest("t", "b2", ["x", "y"])


def test_a_non_ascii_file_name_is_recorded_as_the_file_is_named(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*")
    (repo / "café.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cafe")
    run_cli(repo, "complete", "T1", "--sha", _git(repo, "rev-parse", "HEAD"), "--force")
    assert "café.txt" in LG.build(EventLog(repo).read_all(), "T1")["done"]["files"]


def test_js_style_test_files_are_counted_as_tests():
    assert all(LG._TEST.search(f) for f in ("src/a.test.ts", "src/a.spec.js", "pkg/a_test.go"))
    assert not LG._TEST.search("src/contest.py")
    assert not LG._TEST.search("docs/openapi.spec.yaml") and not LG._TEST.search("schema.spec.json")


def test_show_says_unknown_not_zero_when_files_were_not_recorded(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    run_cli(repo, "complete", "T1", "--force")
    assert "UNKNOWN" in run_cli(repo, "show", "T1")[1]
    assert json.loads(run_cli(repo, "--json", "show", "T1")[1])["ledger"]["files_known"] is False


def test_a_reconstructed_ledger_still_detects_a_later_requirement_edit(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.py")
    log = EventLog(repo)
    log.append(
        "item.completed", "T1", {"sha": "", "kind": "task", "forced": False, "overridden": []}
    )
    run_cli(repo, "update", "T1", "--body", "rewritten later")
    led = LG.build(log.read_all(), "T1")
    assert led["reconstructed"] and led["requirement_changed_after"] is True
