"""`onboard preflight --apply` exits 3 when it refused an item (bug B32be97b560).

The api built `removed`, `refused` and `failed` but tested only `failed`, so an approval
naming nothing (`--only <typo>`) or a live-leased tree fell through to exit 0: a
script reading the exit code was told everything it asked for was done.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import git_quiet as _git

from ddflow.api import onboard as api_onboard

OK, REFUSED = 0, 3


def _merged_branch(repo: Path, name: str) -> None:
    _git(repo, "checkout", "-qb", name)
    (repo / f"{name}.py").write_text("x\n")
    _git(repo, "add", f"{name}.py")
    _git(repo, "commit", "-qm", f"add {name}")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", name)


def _exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            capture_output=True,
        ).returncode
        == 0
    )


def test_an_approval_that_matches_nothing_exits_refused(repo):
    _merged_branch(repo, "landed")

    out = api_onboard.preflight(repo, apply=True, only=["lnaded"])

    assert out.exit == REFUSED, (out.exit, out.reason)
    assert [r["name"] for r in out.data["refused"]] == ["lnaded"]
    assert "lnaded" in out.reason
    assert _exists(repo, "landed")


def test_a_partly_refused_approval_still_removes_the_rest_and_exits_refused(repo):
    _merged_branch(repo, "one")

    out = api_onboard.preflight(repo, apply=True, only=["one", "typo"])

    assert out.exit == REFUSED, (out.exit, out.reason)
    assert [r["name"] for r in out.data["removed"]] == ["one"]
    assert [r["name"] for r in out.data["refused"]] == ["typo"]
    assert not _exists(repo, "one")


def test_an_approval_that_is_all_done_still_exits_ok(repo):
    _merged_branch(repo, "one")
    out = api_onboard.preflight(repo, apply=True, only=["one"])
    assert out.exit == OK, out.reason


def test_the_cli_exits_with_the_stage_outcome_not_a_traceback(repo):
    """B1aeef09393: `cmd_onboard` lost its `return out.exit`, and `cli.main` does
    `int(args.fn(...))`, so every `ddflow onboard` stage ended in a TypeError."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    _merged_branch(repo, "landed")
    code, out, err = run_cli(repo, "onboard", "preflight", "--apply", "--accept", "lnaded")
    assert "Traceback" not in err and "TypeError" not in err, err
    assert code == REFUSED, (code, out, err)
    assert "lnaded" in err

    code, out, err = run_cli(repo, "onboard", "preflight")
    assert code == OK, (code, err)
    assert "landed" in out
