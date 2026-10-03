"""`ddflow verify --all/--phase`: every done task checked, worst first (B-verify-sweep)."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.api.verify import verify_sweep
from ddflow.core import outcome as O


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


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
