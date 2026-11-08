"""A failed review names the reviewer that found the blocking defect (bug B3113b1f522).

With several reviewers in one round, `recorded <item>.<gate> = failed (reviewer X)` used to
name whichever reviewer `_identifying_reviewer` ranked as the identifying pass -- the first
reviewer that ran, or a clean cross-family one -- even when a DIFFERENT reviewer is the one
whose finding failed the gate. A clean cross-family reviewer (or the author's own family,
listed first) reporting zero findings was recorded as the reviewer that failed the gate,
hiding whose finding actually blocked it.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _clean(path: Path) -> str:
    path.write_text("#!/bin/sh\ncat > /dev/null\necho 'STATUS: NO FINDINGS'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _failing(path: Path) -> str:
    path.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        "echo 'FINDING HIGH x.py:1'\necho 'this change is wrong'\n"
        "echo 'STATUS: FINDINGS 1'\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup(repo: Path, tmp_path: Path) -> None:
    """A deepseek author whose critic gate goes to a clean cross-family reviewer (google,
    listed first) and a finding-holder (xai, listed second). The gate fails on the xai
    finding; the recorded identifying reviewer must be xai, not the clean google peer."""
    clean = _clean(tmp_path / "clean")
    failing = _failing(tmp_path / "failing")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "clean"\nkind = "command"\ncommand = "{clean}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "failing"\nkind = "command"\ncommand = "{failing}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_a_failed_review_names_the_reviewer_that_found_the_blocking_defect(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.data  # REVIEWED (completed), the verdict is `failed` in outcome
    assert out.data["outcome"] == "failed", out.data
    ev = _ev(repo)
    assert ev["reviewer"] == "failing", ev
    assert ev["family"] == "xai", ev


def _partial_failing(path: Path) -> str:
    """A reviewer that answers the chunk carrying FINDME with a HIGH finding and any other
    chunk off-contract (no STATUS: block), so it is PARTIAL with a blocking finding."""
    path.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo thinking;; esac\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup_partial(repo: Path, tmp_path: Path) -> None:
    """A deepseek author whose critic gate goes to a clean REVIEWED peer (google) and a
    PARTIAL failing peer (xai). The gate fails on the xai finding; the run must still exit
    OK (a clean REVIEWED pass identifies it), and the record must name xai."""
    clean = _clean(tmp_path / "clean")
    failing = _partial_failing(tmp_path / "failing")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "clean"\nkind = "command"\ncommand = "{clean}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\nmax_chunk_chars = 100\n'
        f'[[reviewer]]\nname = "failing"\nkind = "command"\ncommand = "{failing}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\nmax_chunk_chars = 100\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\n" + "p" * 80 + "\n")
    (repo / "y.py").write_text("y = 2\n" + "p" * 80 + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_a_partial_failing_reviewer_keeps_the_run_exit_ok(repo, tmp_path):
    _setup_partial(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.data  # the run completed (a clean REVIEWED peer identifies it)
    assert out.data["outcome"] == "failed", out.data
    ev = _ev(repo)
    assert ev["reviewer"] == "failing", ev


def _dead(path: Path) -> str:
    """A reviewer whose every chunk comes back off-contract (no STATUS: block), so it
    reviewed NOTHING and its result is UNAVAILABLE."""
    path.write_text("#!/bin/sh\ncat > /dev/null\necho thinking\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup_dead_partial(repo: Path, tmp_path: Path) -> None:
    """A dead reviewer (UNAVAILABLE, listed first) and a PARTIAL failing peer (xai, listed
    second). The gate must record the PARTIAL finding, not read 'unavailable' from the dead
    reviewer and drop it (bug B-review-worst-reviewer-misstates-coverage)."""
    dead = _dead(tmp_path / "dead")
    failing = _partial_failing(tmp_path / "failing")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "dead"\nkind = "command"\ncommand = "{dead}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\nmax_chunk_chars = 100\n'
        f'[[reviewer]]\nname = "failing"\nkind = "command"\ncommand = "{failing}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\nmax_chunk_chars = 100\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\n" + "p" * 80 + "\n")
    (repo / "y.py").write_text("y = 2\n" + "p" * 80 + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_a_dead_reviewer_does_not_hide_a_partial_peers_finding(repo, tmp_path):
    """An UNAVAILABLE reviewer named over a PARTIAL one that found a finding misstates the
    gate's coverage and drops the finding; the PARTIAL reviewer is the identifying pass
    (bug B-review-worst-reviewer-misstates-coverage)."""
    _setup_dead_partial(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] in ("partial", "failed"), out.data
    assert out.data["outcome"] != "unavailable", out.data
    ev = _ev(repo)
    assert ev["findings"] == 1, ev
    assert ev["reviewer"] == "failing", ev
