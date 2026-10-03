"""Completions from before the ledger are verified against the landing git still shows (B-verify-backfill)."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.api.verify import verify
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


def _old_completion(repo, tid="T1", **extra):
    """A completion as written before ledgers: no `ledger` key, no sha."""
    EventLog(repo).append(
        "item.completed", tid, {"kind": "task", "forced": False, "overridden": [], **extra}
    )


def _claims(out):
    return {c["id"]: c for c in out.data["claims"]}


def _setup(repo, tid="T1", globs="w.py,tests/test_w.py"):
    run_cli(repo, "init")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    run_cli(repo, "task", "add", tid, "--title", "add widget", "--globs", globs)


def test_the_landing_is_found_by_the_subject_that_names_the_item(repo):
    _setup(repo)
    _commit(
        repo,
        {"w.py": "1\n", "tests/test_w.py": "def test_w():\n    pass\n"},
        "merge T1: add the widget",
    )
    _old_completion(repo, imported=True, evidence="closed in BACKLOG")
    out = verify(repo, "T1")
    cl = _claims(out)
    assert cl["landed"]["status"] == "ok" and cl["survives"]["status"] == "ok"
    assert cl["ledger"]["status"] == "warn" and "reconstructed" in cl["ledger"]["detail"]
    assert "subject names T1" in cl["ledger"]["detail"]


def test_the_merge_sha_the_log_recorded_is_preferred(repo):
    _setup(repo)
    sha = _commit(
        repo, {"w.py": "1\n", "tests/test_w.py": "def test_w():\n    pass\n"}, "unrelated words"
    )
    EventLog(repo).append("worktree.merged", "T1", {"sha": sha, "branch": "x"})
    _old_completion(repo, imported=True, evidence="closed")
    cl = _claims(verify(repo, "T1"))
    assert cl["landed"]["status"] == "ok" and "merge sha the log recorded" in cl["ledger"]["detail"]


def test_no_landing_found_stays_cannot_tell(repo):
    _setup(repo)
    _old_completion(repo, imported=True, evidence="closed")
    out = verify(repo, "T1")
    assert _claims(out)["ledger"]["status"] == "unknown"
    assert "landed" not in _claims(out)


def test_a_longer_id_and_a_body_mention_are_not_the_landing(repo):
    _setup(repo)
    _commit(repo, {"x.py": "1\n"}, "merge T10: something else")
    _commit(repo, {"y.py": "1\n"}, "tidy\n\nfollow-up to merge T1: add the widget")
    _old_completion(repo, imported=True, evidence="closed")
    assert "landed" not in _claims(verify(repo, "T1"))


def test_a_reconstructed_landing_with_missing_declared_files_still_fails(repo):
    _setup(repo, globs="never/there.py")
    _commit(repo, {"other.py": "1\n"}, "merge T1: add the widget")
    _old_completion(repo, imported=True, evidence="closed")
    out = verify(repo, "T1")
    assert (
        _claims(out)["declared_files"]["status"] == "fail"
        and out.data["verdict"] == "does not hold"
    )


def test_backfill_never_writes_to_the_log(repo):
    _setup(repo)
    _commit(repo, {"w.py": "1\n"}, "merge T1: add the widget")
    _old_completion(repo, imported=True, evidence="closed")
    before = len(EventLog(repo).read_all())
    verify(repo, "T1")
    assert len(EventLog(repo).read_all()) == before


def test_a_contemporaneous_ledger_is_never_replaced_by_a_search(repo):
    _setup(repo)
    sha = _commit(
        repo,
        {"w.py": "1\n", "tests/test_w.py": "def test_w():\n    pass\n"},
        "merge T1: add the widget",
    )
    from ddflow.core.model import fold
    from ddflow.services import ledger as LG

    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    EventLog(repo).append(
        "item.completed", "T1",
        {"sha": sha, "kind": "task", "forced": True, "overridden": [], "ledger": LG.git_facts(repo, sha, it)},
    )  # fmt: skip
    cl = _claims(verify(repo, "T1"))
    assert "ledger" not in cl  # a stored ledger needs no reconstruction note
