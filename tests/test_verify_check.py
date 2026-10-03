"""`ddflow verify <id>`: the claims behind a completion, re-derived (B-verify-check).

Each test builds a completion that is false in one specific way -- the kind this project
actually shipped: a declared file that never existed, a commit that is not on main -- and
asserts the check says so, and that an honest completion is not accused.
"""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.api.verify import verify
from ddflow.core import outcome as O
from ddflow.infra.log import EventLog


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, files: dict[str, str], msg="work"):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _task(repo, globs, tid="T1"):
    run_cli(repo, "task", "add", tid, "--title", "do it", "--globs", globs)


def _claims(out):
    return {c["id"]: c for c in out.data["claims"]}


def test_an_honest_completion_is_not_accused(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py,tests/test_w.py")
    sha = _commit(repo, {"w.py": "x = 1\n", "tests/test_w.py": "def test_w():\n    pass\n"})
    for g in ("research", "rules", "implement", "rubber_duck", "critic", "standards"):
        run_cli(repo, "gate", "record", "T1", g, "--outcome", "passed", "--evidence", "e")
    for g in ("unit_tests", "bug_hunt", "dedupe", "merge"):
        run_cli(repo, "gate", "record", "T1", g, "--outcome", "passed", "--evidence", "e")
    # `complete` itself demands a proven independent reviewer; verify is tested on the
    # completion event it would write, with the ledger the real path writes.
    from ddflow.core.model import fold
    from ddflow.services import ledger as LG

    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    EventLog(repo).append(
        "item.completed",
        "T1",
        {
            "sha": sha,
            "kind": "task",
            "forced": False,
            "overridden": [],
            "ledger": LG.git_facts(repo, sha, it),
        },
    )
    out = verify(repo, "T1")
    cl = _claims(out)
    assert out.exit == O.OK, out.reason
    assert cl["landed"]["status"] == "ok" and cl["declared_files"]["status"] == "ok"
    assert cl["tests"]["status"] == "ok" and cl["survives"]["status"] == "ok"
    assert cl["gates"]["status"] == "ok", cl["gates"]
    assert out.data["verdict"] == "holds"

    # and the same completion forced past its gates is flagged
    run_cli(repo, "task", "add", "T2", "--title", "again", "--globs", "w.py")
    run_cli(repo, "complete", "T2", "--sha", sha, "--force")
    assert _claims(verify(repo, "T2"))["gates"]["status"] == "fail"


def test_a_declared_file_that_was_never_created_fails(repo):
    run_cli(repo, "init")
    _task(repo, "ddflow/surfaces/commands/rules.py,tests/test_rules_cli.py")
    sha = _commit(repo, {"unrelated.txt": "x\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    out = verify(repo, "T1")
    assert out.exit == O.FAIL
    assert _claims(out)["declared_files"]["status"] == "fail"
    assert "rules.py" in out.reason and out.data["verdict"] == "does not hold"


def test_a_commit_that_is_not_in_the_repository_fails(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    run_cli(repo, "complete", "T1", "--sha", "0123456789abcdef0123456789abcdef01234567", "--force")
    out = verify(repo, "T1")
    assert out.exit == O.FAIL and _claims(out)["landed"]["status"] == "fail"


def test_a_commit_on_an_unmerged_branch_is_not_landed(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    _commit(repo, {"base.txt": "b\n"}, "base")
    _git(repo, "checkout", "-q", "-b", "side")
    sha = _commit(repo, {"w.py": "x = 1\n"})
    _git(repo, "checkout", "-q", "-")
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    out = verify(repo, "T1")
    assert _claims(out)["landed"]["status"] == "fail"


def test_a_skipped_gate_with_no_reason_fails(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    sha = _commit(repo, {"w.py": "x = 1\n"})
    EventLog(repo).append("gate.skipped", "T1", {"gate": "docs"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    out = verify(repo, "T1")
    assert _claims(out)["gates"]["status"] == "fail" and "docs" in _claims(out)["gates"]["detail"]


def test_code_with_no_test_is_a_warning_not_a_failure(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    sha = _commit(repo, {"w.py": "x = 1\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    cl = _claims(verify(repo, "T1"))
    assert cl["tests"]["status"] == "warn" and "no test" in cl["tests"]["detail"]


def test_files_removed_afterwards_are_reported(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "a.py,b.py")
    sha = _commit(repo, {"a.py": "1\n", "b.py": "2\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    _git(repo, "rm", "-q", "a.py")
    _git(repo, "commit", "-qm", "drop a")
    assert _claims(verify(repo, "T1"))["survives"]["status"] == "warn"
    _git(repo, "rm", "-q", "b.py")
    _git(repo, "commit", "-qm", "drop b")
    out = verify(repo, "T1")
    assert _claims(out)["survives"]["status"] == "fail" and out.exit == O.FAIL


def test_a_closed_bug_with_a_missing_regression_test_file_fails(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "bug", "found", "--new", "--summary", "it breaks", "--id", "BX")
    fix = "fix-BX"
    sha = _commit(repo, {"w.py": "x = 1\n", "tests/test_nope.py": "def test_x():\n    pass\n"})
    code, out, err = run_cli(
        repo, "bug", "fixed", "BX", "--regression-test", "tests/test_nope.py::test_x"
    )
    assert code == 0, out + err
    run_cli(repo, "complete", fix, "--sha", sha, "--force")
    _git(repo, "rm", "-q", "tests/test_nope.py")
    _git(repo, "commit", "-qm", "oops, deleted the regression test")
    cl = _claims(verify(repo, fix))
    assert cl["regression"]["status"] == "fail" and "test_nope.py" in cl["regression"]["detail"]


def test_a_task_that_is_not_done_has_nothing_to_verify(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    out = verify(repo, "T1")
    assert out.exit == O.NOTHING and out.data["verdict"] == "not completed"


def test_a_completion_without_recorded_files_is_cannot_tell_not_holds(repo):
    run_cli(repo, "init")
    _task(repo, "w.py")
    run_cli(repo, "complete", "T1", "--force")
    out = verify(repo, "T1")
    assert out.data["verdict"] in ("cannot tell", "does not hold")
    assert _claims(out)["landed"]["status"] == "unknown"


def test_the_cli_exits_1_on_a_failed_claim_and_2_on_an_open_task(repo):
    import json

    run_cli(repo, "init")
    _task(repo, "never/created.py")
    sha = _commit(repo, {"other.txt": "x\n"})
    assert run_cli(repo, "verify", "T1")[0] == 2  # not done yet
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    code, out, _ = run_cli(repo, "verify", "T1")
    assert code == 1 and "does not hold" in out and "FAIL" in out and "never/created.py" in out
    code, out, _ = run_cli(repo, "--json", "verify", "T1")
    body = json.loads(out)
    assert code == 1 and body["verdict"] == "does not hold"
    assert any(c["id"] == "declared_files" and c["status"] == "fail" for c in body["claims"])


def test_ddflow_config_and_rules_changes_stay_in_the_ledger_but_event_shards_do_not(repo):
    from ddflow.core.model import fold
    from ddflow.services import ledger as LG

    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, ".ddflow/rules/r-x.toml")
    sha = _commit(repo, {".ddflow/rules/r-x.toml": 'id = "r-x"\n', "w.py": "1\n"})
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    files = LG.git_facts(repo, sha, it)["files"]
    assert ".ddflow/rules/r-x.toml" in files and not any(
        f.startswith(".ddflow/events/") for f in files
    )


def test_on_gitflow_a_commit_only_on_production_is_a_warning_not_ok(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[flow]\nmodel = "gitflow"\n')
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _git(repo, "branch", "develop")
    _task(repo, "w.py")
    sha = _commit(repo, {"w.py": "1\n"})  # on the default branch only
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    assert _claims(verify(repo, "T1"))["landed"]["status"] == "warn"


def test_a_landing_that_changed_no_files_is_not_reported_as_ok(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    run_cli(repo, "complete", "T1", "--sha", _git(repo, "rev-parse", "HEAD"), "--force")
    cl = _claims(verify(repo, "T1"))
    assert cl["tests"]["status"] == "warn" and cl["survives"]["status"] == "warn"


def test_a_declared_file_removed_later_is_a_warning_but_one_never_created_is_a_failure(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _commit(repo, {"old.py": "1\n"}, "old")  # existed before the task
    _git(repo, "rm", "-q", "old.py")
    _git(repo, "commit", "-qm", "removed")
    _task(repo, "old.py")
    sha = _commit(repo, {"other.py": "1\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    assert _claims(verify(repo, "T1"))["declared_files"]["status"] == "warn"
    _task(repo, "never.py", "T2")
    run_cli(repo, "complete", "T2", "--sha", sha, "--force")
    assert _claims(verify(repo, "T2"))["declared_files"]["status"] == "fail"


def test_a_file_that_only_a_sibling_branch_created_does_not_excuse_a_false_completion(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _git(repo, "checkout", "-q", "-b", "sibling")
    _commit(repo, {"declared.py": "1\n"}, "sibling work")
    _git(repo, "checkout", "-q", "-")
    _task(repo, "declared.py")
    sha = _commit(repo, {"other.py": "1\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    assert _claims(verify(repo, "T1"))["declared_files"]["status"] == "fail"


def _complete_with(repo, outcomes: dict[str, tuple[str, str]], sha=""):
    """Record every pipeline gate, then the completion event, as a real run would."""
    from ddflow.core.model import fold
    from ddflow.services import ledger as LG

    for g in ("research", "rules", "implement", "rubber_duck", "critic", "standards"):
        o, why = outcomes.get(g, ("passed", ""))
        EventLog(repo).append(f"gate.{o}", "T1", {"gate": g, **({"reason": why} if why else {})})
    for g in ("unit_tests", "bug_hunt", "dedupe", "merge"):
        o, why = outcomes.get(g, ("passed", ""))
        EventLog(repo).append(f"gate.{o}", "T1", {"gate": g, **({"reason": why} if why else {})})
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    EventLog(repo).append(
        "item.completed",
        "T1",
        {
            "sha": sha,
            "kind": "task",
            "forced": False,
            "overridden": [],
            "ledger": LG.git_facts(repo, sha, it),
        },
    )


def test_a_failed_review_gate_that_is_not_required_is_a_note_but_a_required_skip_fails(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py,tests/test_w.py")
    sha = _commit(repo, {"w.py": "1\n", "tests/test_w.py": "def test_w():\n    pass\n"})
    _complete_with(repo, {"rubber_duck": ("failed", "")}, sha)
    g = _claims(verify(repo, "T1"))["gates"]
    assert g["status"] == "warn" and "rubber_duck" in g["detail"]


def test_a_required_gate_that_was_skipped_fails_even_with_a_reason(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py")
    sha = _commit(repo, {"w.py": "1\n"})
    _complete_with(repo, {"unit_tests": ("skipped", "too slow")}, sha)
    g = _claims(verify(repo, "T1"))["gates"]
    assert g["status"] == "fail" and "unit_tests" in g["detail"]


def test_a_task_imported_as_already_closed_is_cannot_tell_not_an_accusation(repo):
    run_cli(repo, "init")
    _commit(repo, {"w.py": "1\n"}, "w exists")
    _task(repo, "w.py")
    EventLog(repo).append(
        "item.completed",
        "T1",
        {"imported": True, "kind": "task", "evidence": "closed in docs/BACKLOG.md:1"},
    )
    out = verify(repo, "T1")
    assert out.exit == O.OK and out.data["verdict"] == "cannot tell"
    assert {c["id"] for c in out.data["claims"]} == {"declared_files", "ledger"}


def test_history_rewritten_since_is_a_warning_when_main_has_a_commit_with_the_same_subject(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py")
    _git(repo, "checkout", "-q", "-b", "old")
    (repo / "w.py").write_text("1\n")
    _git(repo, "add", "w.py")  # not -A: the queue's own files must stay on the main line
    _git(repo, "commit", "-qm", "add the widget module for the queue")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-")
    _commit(repo, {"w.py": "1\n"}, "add the widget module for the queue")  # rewritten twin
    run_cli(repo, "complete", "T1", "--sha", old, "--force")
    landed = _claims(verify(repo, "T1"))["landed"]
    assert landed["status"] == "warn" and "rewritten" in landed["detail"]


def test_a_commit_that_merely_mentions_the_subject_in_its_body_is_not_a_twin(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, "w.py")
    _git(repo, "checkout", "-q", "-b", "old")
    (repo / "w.py").write_text("1\n")
    _git(repo, "add", "w.py")
    _git(repo, "commit", "-qm", "add the widget module for the queue")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-")
    _commit(
        repo, {"unrelated.py": "1\n"}, "tidy\n\nfollow-up to: add the widget module for the queue"
    )
    run_cli(repo, "complete", "T1", "--sha", old, "--force")
    assert _claims(verify(repo, "T1"))["landed"]["status"] == "fail"


def test_an_untracked_file_and_a_declared_directory_that_exist_are_not_never_created(repo):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    _task(repo, ".mcp.json,local-dir/")
    sha = _commit(repo, {"other.py": "1\n"})
    # created AFTER the commit and never added: git has never seen either
    (repo / ".mcp.json").write_text("{}\n")
    (repo / "local-dir").mkdir()
    (repo / "local-dir" / "x.txt").write_text("x\n")
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    d = _claims(verify(repo, "T1"))["declared_files"]
    assert d["status"] != "fail" and "never created" not in d["detail"], d
