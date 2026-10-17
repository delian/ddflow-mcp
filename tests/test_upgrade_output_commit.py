"""upgrade-output-needs-lease-to-commit: what `ddflow upgrade --apply` wrote can be committed
without a lease, and only as written.

The commit hook refuses a path no lease covers. The upgrade rewrites files the hook does not
treat as ddflow's own (.claude/settings.json, ...), so the agent that ran it could not commit
its output without inventing a task. The apply now records the digest of every file it wrote;
the hook waves a staged file through when its staged bytes are exactly those.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_upgrade_apply import go, old  # noqa: F401 -- the old-version project fixture

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.outcome import FAIL, OK
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import upgrade_apply as UA

SETTINGS = ".claude/settings.json"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _verdict(repo: Path):
    cfg = Config.load(repo)
    cfg.enforce.commit_without_lease = "block"
    return E.check_commit(repo, cfg, agent="upgrader")


def test_the_output_of_an_upgrade_commits_without_a_lease(old: Path) -> None:  # noqa: F811
    out = go(old, "hooks")
    assert out["exit"] == 0 and (old / SETTINGS).exists(), out["text"]
    _git(old, "add", SETTINGS)
    verdict = _verdict(old)
    assert verdict.exit == OK, verdict.message


def test_a_whole_apply_commits_as_one_without_a_lease(old: Path) -> None:  # noqa: F811
    out = go(old, None, config_changes="agent")
    assert out["applied"] > 1, out["text"]
    _git(old, "add", "-A")
    staged = subprocess.run(
        ["git", "-C", str(old), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert SETTINGS in staged and "AGENTS.md" in staged, staged
    verdict = _verdict(old)
    assert verdict.exit == OK, verdict.message


def test_the_digests_of_a_run_that_settled_nothing_are_kept_too(old: Path) -> None:  # noqa: F811
    """A run that wrote files and then settled no item closes with a failure note: the files are
    on disk all the same, and the note must name them."""
    log = EventLog(old, "upgrader")
    UA._close(log, "r1", [], set(), ("1", "1", "2"), [], "", {}, False, {SETTINGS: "abc"})
    assert fold(log.read_all(), strict=False).upgrade_runs["r1"]["written"] == {SETTINGS: "abc"}


def test_the_output_of_an_interrupted_upgrade_commits_too(old: Path) -> None:  # noqa: F811
    """A run killed after its first step never reaches the closing event: each step carries the
    digests of what was written so far."""

    def killed(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        go(old, "hooks", watch=UA.Watch(progress=killed))
    assert (old / SETTINGS).exists()
    _git(old, "add", SETTINGS)
    verdict = _verdict(old)
    assert verdict.exit == OK, verdict.message


def test_a_later_edit_of_that_output_still_needs_a_lease(old: Path) -> None:  # noqa: F811
    go(old, "hooks")
    with (old / SETTINGS).open("a") as f:
        f.write("\n")
    _git(old, "add", SETTINGS)
    verdict = _verdict(old)
    assert verdict.exit == FAIL and SETTINGS in verdict.message
