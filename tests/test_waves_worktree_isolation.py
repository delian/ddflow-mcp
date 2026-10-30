"""Bug Bf4eaec64a6: a command run from an item worktree wrote to the LIVE primary event log.

While building `[waves].auto_stamp`, running the worktree's `ddflow waves` against the live
queue triggered the post-command auto-stamp, which appended 1170 `wave:N` tag events to an
untracked shard of the PRIMARY checkout's `.ddflow/events/`. Every worktree of a clone
shares that one log (`worktree.repo_root`); a feature under test running against ITS OWN
primary must never write it.

The hook refuses to run then: the ddflow package that is RUNNING lives in a linked
worktree whose primary is the repository being managed. Running the same worktree's code
against some OTHER repository (a temp checkout, another project) writes that repository's
own log -- as the suite does -- and is left alone.
"""

from __future__ import annotations

import pytest
from conftest import run_cli, write_config
from helpers import git_quiet as _git
from helpers import items_of

from ddflow.infra import paths as PATHS


@pytest.fixture(autouse=True)
def _default_stamping(monkeypatch):
    """The suite turns auto-stamp off (conftest); this tests the shipped default."""
    monkeypatch.delenv("DDFLOW_WAVES_AUTO_STAMP")


def _waves(repo) -> dict[str, str]:
    return {
        i: t
        for i, it in items_of(repo).items()
        for t in it.tags
        if t.startswith("wave:") and not it.terminal
    }


@pytest.fixture
def ddflow_source(tmp_path):
    """A primary checkout that is BOTH a ddflow source tree and a ddflow-managed project,
    like the real primary -- so a linked worktree of it is `primary_checkout`'s picture of
    "the code under test"."""
    p = tmp_path / "ddflow"
    (p / "ddflow").mkdir(parents=True)
    (p / "ddflow" / "__init__.py").write_text("")
    _git(p, "init", "-q", "-b", "main")
    _git(p, "-c", "user.email=t@e", "-c", "user.name=t", "add", ".")
    _git(p, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "x")
    return p


def test_running_worktree_code_against_its_primary_is_detected(
    ddflow_source, tmp_path, monkeypatch
):
    p = ddflow_source
    tree = tmp_path / "wt"
    _git(p, "worktree", "add", "-q", str(tree), "-b", "feature")
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    assert PATHS.running_from_linked_worktree_against(p) is True
    monkeypatch.setattr(PATHS, "package_parent", lambda: p)
    assert PATHS.running_from_linked_worktree_against(p) is False
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    assert PATHS.running_from_linked_worktree_against(tmp_path / "elsewhere") is False


def test_a_worktree_command_against_its_own_primary_does_not_auto_stamp_the_live_log(
    ddflow_source, tmp_path, monkeypatch
):
    from ddflow.api import waves_auto
    from ddflow.config import Config
    from ddflow.services import identity as ID

    p = ddflow_source
    # Build the live queue with auto-stamp off so the setup's own events are just the queue.
    run_cli(p, "init")
    write_config(p, [("waves.auto_stamp", False)])
    run_cli(p, "task", "add", "A", "--globs", "a.py", "--title", "A")
    assert _waves(p) == {}

    # The RUNNING ddflow is a linked worktree of THAT primary (the code under test).
    tree = tmp_path / "wt"
    _git(p, "worktree", "add", "-q", str(tree), "-b", "feature")
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)

    cfg = Config.load(p)
    cfg.waves.auto_stamp = True
    log, _who = ID.open_log(p, cfg, "probe")
    # A command that APPENDS an event would normally trigger a restamp; the running code is
    # the worktree of the LIVE log's primary, so no `wave:*` tag may land in it.
    with waves_auto.keep_stamped(p, cfg, log, "probe"):
        log.append("session.note", "probe", {"text": "a command that wrote"})
    assert _waves(p) == {}


def test_a_worktree_command_against_another_repo_still_auto_stamps(
    ddflow_source, tmp_path, monkeypatch
):
    """The guard is NOT blanket off for worktree code: targeting a repository that is not
    the worktree's primary writes that repository's own log, and auto-stamping is normal
    (as the suite does when ddflow's own tests run from an item worktree)."""
    from ddflow.api import waves_auto
    from ddflow.config import Config
    from ddflow.services import identity as ID

    other = tmp_path / "proj"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(
        other,
        "-c",
        "user.email=t@e",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    run_cli(other, "init")
    write_config(other, [("waves.auto_stamp", False)])
    run_cli(other, "task", "add", "A", "--globs", "a.py", "--title", "A")
    assert _waves(other) == {}

    tree = tmp_path / "wt"
    _git(ddflow_source, "worktree", "add", "-q", str(tree), "-b", "feature")
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)

    cfg = Config.load(other)
    cfg.waves.auto_stamp = True
    log, _who = ID.open_log(other, cfg, "probe")
    with waves_auto.keep_stamped(other, cfg, log, "probe"):
        log.append("session.note", "probe", {"text": "a command that wrote"})
    assert _waves(other) == {"A": "wave:0"}
