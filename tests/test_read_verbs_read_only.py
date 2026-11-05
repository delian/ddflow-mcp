"""Bug Bc5d4aafb98: a read verb restamped the waves whenever ANOTHER agent appended.

`keep_stamped` compared the global `log.mark()`, so a second process writing while `show`,
`list`, `board` or `next` ran made the read take the lock, fold the queue and call
`batch.apply`. Rule r-read-verbs-read-only: only the caller's own appends may stamp.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest
from conftest import run_cli, write_config

from ddflow.api import batch, waves_auto
from ddflow.config import Config
from ddflow.infra.log import EventLog


@pytest.fixture(autouse=True)
def _default_stamping(monkeypatch):
    monkeypatch.delenv("DDFLOW_WAVES_AUTO_STAMP", raising=False)


def _setup(repo):
    run_cli(repo, "init")
    assert not write_config(repo, [("waves.auto_stamp", False)])[0]
    run_cli(repo, "task", "add", "A", "--globs", "a.py", "--title", "A")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A", "--title", "B")
    assert not write_config(repo, [("waves.auto_stamp", True)])[0]  # waves now stale


def _count(repo) -> int:
    return len(list(EventLog(repo, "probe").read_all()))


def test_foreign_appends_during_a_read_do_not_restamp(repo, monkeypatch):
    _setup(repo)
    calls = []
    monkeypatch.setattr(batch, "apply", lambda *a, **k: calls.append(1))
    cfg = Config.load(repo)
    mine = EventLog(repo, "reader")
    other = EventLog(repo, "writer")
    with waves_auto.keep_stamped(repo, cfg, mine, "reader"):
        for i in range(3):  # a second agent appends in a loop while the read runs
            other.append("session.note", f"n{i}", {"text": "x"})
    assert calls == []


def test_own_appends_still_restamp(repo, monkeypatch):
    _setup(repo)
    calls = []
    monkeypatch.setattr(batch, "apply", lambda *a, **k: calls.append(1))
    cfg = Config.load(repo)
    mine = EventLog(repo, "me")
    with waves_auto.keep_stamped(repo, cfg, mine, "me"):
        mine.append("session.note", "n", {"text": "x"})
    assert calls == [1]


def test_read_verbs_append_nothing_while_another_agent_writes(repo):
    """The CLI end to end: a second writer appends in a loop while the read verbs run;
    every event added meanwhile is the writer's, none is a restamp."""
    _setup(repo)
    before = {e.id for e in EventLog(repo, "probe").read_all()}
    stop, started, errors = threading.Event(), threading.Event(), []

    def writer():
        try:
            other, n = EventLog(repo, "writer"), 0
            while not stop.is_set():
                other.append("session.note", f"n{n}", {"text": "x"})
                started.set()
                n += 1
                time.sleep(0.05)
        except Exception as exc:  # surfaced below, not swallowed with the thread
            errors.append(exc)
            started.set()

    t = threading.Thread(target=writer)
    t.start()
    started.wait(30)
    try:
        for verb in (("show", "A"), ("list",), ("board",), ("next",)):
            run_cli(repo, *verb)
    finally:
        stop.set()
        t.join()
    added = [e for e in EventLog(repo, "probe").read_all() if e.id not in before]
    assert not errors
    assert added and {e.agent for e in added} == {"writer"}


def test_second_process_with_the_same_agent_name_does_not_restamp(repo, monkeypatch):
    """A shard is keyed by agent name: another PROCESS appending under the caller's own
    name grows the caller's shard, and still is not the caller's write."""
    _setup(repo)
    calls = []
    monkeypatch.setattr(batch, "apply", lambda *a, **k: calls.append(1))
    cfg = Config.load(repo)
    mine = EventLog(repo, "shared")
    code = (
        "import sys; from ddflow.infra.log import EventLog; "
        "EventLog(sys.argv[1], 'shared').append('session.note', 'n', {'text': 'x'})"
    )
    with waves_auto.keep_stamped(repo, cfg, mine, "shared"):
        subprocess.run([sys.executable, "-c", code, str(repo)], check=True)
    assert calls == []


def test_other_thread_with_the_same_agent_name_does_not_restamp(repo, monkeypatch):
    _setup(repo)
    calls = []
    monkeypatch.setattr(batch, "apply", lambda *a, **k: calls.append(1))
    cfg = Config.load(repo)
    mine = EventLog(repo, "shared")

    def other():
        EventLog(repo, "shared").append("session.note", "n", {"text": "x"})

    with waves_auto.keep_stamped(repo, cfg, mine, "shared"):
        t = threading.Thread(target=other)  # a fresh thread: a context of its own
        t.start()
        t.join()
    assert calls == []
