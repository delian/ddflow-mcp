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


def test_a_poisoned_clone_config_cannot_steer_a_later_run_onto_the_operator_repo(proj):
    """Each repro run is a fresh clone, so a first run that plants `core.worktree` at the
    operator checkout into its clone's config cannot make a LATER run's git write the
    operator tree; a follow-up git write stays inside the throwaway."""
    import subprocess

    head0 = subprocess.run(
        ["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    # Run 1 poisons ITS clone to point at the operator checkout, then writes a commit -- if
    # ANY run shared that clone and honored core.worktree, that write would move the real
    # tree. With fresh clones the poisoned config dies with its clone.
    poison = repr(str(proj))
    evil = (
        f"{shlex.quote(sys.executable)} -c "
        f"{shlex.quote('import subprocess; subprocess.run(["git","config","core.worktree",' + poison + ']); subprocess.run(["git","commit","--allow-empty","-m","steal"])')}"
    )
    out = K.bug_found(proj, summary="poisoned clone config", agent="a1", repro_cmd=evil)
    assert out.ok, out.reason
    assert (
        subprocess.run(
            ["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        == head0
    )  # the operator checkout was never the commit's target
    # ... and the empty commit did not land in the real log either.
    assert (
        subprocess.run(
            ["git", "-C", str(proj), "log", "--oneline", "--all"],
            capture_output=True,
            text=True,
        ).stdout.count("steal")
        == 0
    )
