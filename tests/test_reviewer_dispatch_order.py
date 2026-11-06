"""Cross-family-first dispatch + up-front shed (bug B19066e802f).

A gate's reviewers ran SEQUENTIALLY in config order, so a slow same-family reviewer
(lan-deepseek-v4-pro) was paid for BEFORE a fast cross-family one (grok-4.7), and a
reviewer already marked degraded/429 (kimi/gemini) was still attempted instead of shed
up-front. The fix ranks the pool so a reviewer OUTSIDE the author's family serves first
and a reviewer that establishes nothing runs last, and it sheds reviewers a prior round
marked degraded before any of them run. This pins both: after one round marks the 429d
reviewer, a later gate sheds it without paying its attempt and the fast cross-family
reviewer runs before the slow same-family one.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api


def _diary_reviewer(tmp_path: Path, name: str, diary: Path, stdout: str, exit_code: int) -> Path:
    """A kind=command reviewer that appends its NAME to the shared ``diary`` (so the review
    is counted and ORDERED by when each reviewer was invoked), then answers ``stdout`` and
    exits ``exit_code``. ``hedge = 1`` keeps one diary entry per reviewer."""
    cli = tmp_path / f"{name}.sh"
    cli.write_text(
        "#!/bin/sh\n"
        "cat > /dev/null\n"
        f'printf "%s\\n" "{name}" >> "{diary}"\n'
        f'printf "%s\\n" "{stdout}"\n'
        f"exit {exit_code}\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def _config(slow: Path, exhausted: Path, fast: Path) -> str:
    # Config order is the bug: the SLOW same-family reviewer FIRST, then the 429d one,
    # then the FAST cross-family one.
    return (
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "slow"\nkind = "command"\ncommand = "{slow}"\n'
        'model = "lan-deepseek-v4-pro"\nfamily = "deepseek"\nhedge = 1\n'
        'gates = ["critic", "rubber_duck"]\n'
        f'[[reviewer]]\nname = "exhausted"\nkind = "command"\ncommand = "{exhausted}"\n'
        'model = "kimi-for-coding-highspeed"\nfamily = "moonshot"\nhedge = 1\n'
        'gates = ["critic", "rubber_duck"]\n'
        f'[[reviewer]]\nname = "fast"\nkind = "command"\ncommand = "{fast}"\n'
        'model = "grok-4.7"\nfamily = "xai"\nhedge = 1\n'
        'gates = ["critic", "rubber_duck"]\n'
    )


def _setup(repo: Path, tmp_path: Path) -> Path:
    diary = tmp_path / "diary.log"
    diary.write_text("")
    slow = _diary_reviewer(tmp_path, "slow", diary, "STATUS: NO FINDINGS", 0)
    exhausted = _diary_reviewer(tmp_path, "exhausted", diary, "HTTP 429: rate limit exceeded", 1)
    fast = _diary_reviewer(tmp_path, "fast", diary, "STATUS: NO FINDINGS", 0)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(_config(slow, exhausted, fast))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add x", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    return diary


def test_cross_family_first_and_degraded_shed_before_any_attempt(repo, tmp_path):
    diary = _setup(repo, tmp_path)

    # Mark the 429d reviewer degraded with its one honest attempt.
    api.review(repo, gate="rubber_duck", item="T1", branch="feat")
    diary.write_text("")

    # The later gate: the degraded reviewer is shed without paying its attempt, and the
    # fast cross-family reviewer runs before the slow same-family one.
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "passed", out.data["text"]
    invoked = diary.read_text().split()
    assert invoked == ["fast", "slow"], invoked
    assert "shed exhausted" in out.data["text"], out.data["text"]
