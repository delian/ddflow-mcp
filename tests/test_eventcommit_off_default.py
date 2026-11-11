"""The event-log commit never strands events off the default branch (Baa48528523).

A door (a release, above all) that runs while the primary checkout sits on an item
branch would commit the event shards THERE, and switching back to the default branch
then drops those events from it -- the shard-marks had already counted them, so
`doctor` reported shards with lost events on the default branch although the events were
only parked on an unmerged branch. The commit is refused while HEAD is off the default
branch and left for the next door that runs on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git


def test_commit_shards_refuses_off_the_default_branch(repo):
    from ddflow.infra.log import EventLog
    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    EventLog(repo, "agent-test").append("task.added", "T1", {"title": "t", "globs": []})

    _git(repo, "checkout", "-qb", "item")  # not the default branch; the event carries over
    sha, note = EC.commit_shards(repo, "release T1")
    assert not sha, (sha, note)
    assert "not the default branch" in note, note
    assert "item" in note, note  # names the branch it would have stranded on
    # It is refused, not stranded: the shard is still uncommitted, and the item branch's
    # history did not gain an events commit off main.
    assert EC.uncommitted_shards(repo), "the event must stay uncommitted off main"
    events = _git(repo, "log", "--format=%s", "item")
    assert "events: release T1" not in events, events

    _git(repo, "checkout", "-q", "main")
    sha, note = EC.commit_shards(repo, "release T1")
    assert sha, note
    assert EC.uncommitted_shards(repo) == []
    assert "events: release T1" in _git(repo, "log", "--format=%s", "main")


def test_commit_shards_commits_when_head_is_on_the_default_branch(repo):
    from ddflow.infra.log import EventLog
    from ddflow.services import eventcommit as EC

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    EventLog(repo, "agent-test").append("task.added", "T1", {"title": "t", "globs": []})
    sha, note = EC.commit_shards(repo, "release T1")
    assert sha and not note, (sha, note)
    assert EC.uncommitted_shards(repo) == []
