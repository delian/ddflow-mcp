"""`bug found --repro-cmd` must not let a mutating repro write the real event log
(bug Bc771f31635).

The repro runs in a throwaway checkout of the recorded sha, so a command that itself
invokes ``ddflow`` (``ddflow cadence --ran NOPE``) must NOT append events to the primary
checkout's shared ``.ddflow/events``. Filing a bug is a read of the project, never a write.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K


@pytest.fixture
def proj(repo: Path, monkeypatch) -> Path:
    run_cli(repo, "init")
    monkeypatch.chdir(repo)
    return repo


def test_a_mutating_repro_does_not_reach_the_shared_log(proj):
    """A repro command that invokes ddflow must append its events to the THROWAWAY checkout,
    never to the primary's tracked `.ddflow/events/`."""
    before = len(list((proj / ".ddflow" / "events").glob("*.jsonl")))
    out = K.bug_found(
        proj,
        summary="repro mutates the shared log",
        agent="a1",
        repro_cmd=f"{shlex.quote(sys.executable)} -m ddflow cadence --ran NOPE",
    )
    assert out.ok, out.reason
    shards = list((proj / ".ddflow" / "events").glob("*.jsonl"))
    for p in shards:
        text = p.read_text()
        assert "cadence.ran" not in text or "NOPE" not in text, (
            f"the repro appended a cadence.ran event into the REAL log {p.name}"
        )
    # A bug record still landed (the filing itself is normal).
    assert state(proj).bugs.get(out.data["id"]) is not None
    assert before <= len(shards)


def test_a_poisoned_dot_git_is_refused_not_followed(proj):
    """The prime refuses a `.git` that is not a real directory: a run that replaced the
    clone's `.git` with a symlink to an operator repo must make the NEXT prime refuse, never
    run `git reset --hard` through the link onto the pointee."""
    import os
    import shutil
    import subprocess

    from ddflow.services import reprocheck as RC

    victim = proj / "victim"
    victim.mkdir()
    subprocess.run(["git", "init", "-q", str(victim)], check=True)
    subprocess.run(["git", "-C", str(victim), "config", "user.email", "e@e"], check=True)
    subprocess.run(["git", "-C", str(victim), "config", "user.name", "n"], check=True)
    (victim / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(victim), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(victim), "commit", "-qm", "one"], check=True)
    head0 = subprocess.run(
        ["git", "-C", str(victim), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (victim / "f.txt").write_text("y")
    subprocess.run(["git", "-C", str(victim), "commit", "-qam", "two"], check=True)
    head1 = subprocess.run(
        ["git", "-C", str(victim), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert head1 != head0

    clone = proj / "clone"
    subprocess.run(["git", "clone", "-q", str(victim), str(clone)], check=True)
    shutil.rmtree(clone / ".git")
    os.symlink(str(victim / ".git"), clone / ".git")
    # A reset that followed the link would move the pointee's HEAD back to head0.
    assert RC._prime_scratch(clone, head0) is False
    assert subprocess.run(
        ["git", "-C", str(victim), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip() == head1  # untouched
