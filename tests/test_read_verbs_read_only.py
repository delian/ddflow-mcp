"""Bug Bc5d4aafb98: a read verb restamped the waves whenever ANOTHER agent appended.

`keep_stamped` compared the global `log.mark()`, so a second process writing while `show`,
`list`, `board` or `next` ran made the read take the lock, fold the queue and call
`batch.apply`. Rule r-read-verbs-read-only: only the caller's own appends may stamp.
"""

from __future__ import annotations

import pytest
from conftest import run_cli, write_config

from ddflow.api import batch, waves_auto
from ddflow.config import Config
from ddflow.infra.log import EventLog


@pytest.fixture(autouse=True)
def _default_stamping(monkeypatch):
    monkeypatch.delenv("DDFLOW_WAVES_AUTO_STAMP")


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


def test_read_verb_with_stale_waves_appends_nothing(repo):
    _setup(repo)
    before = _count(repo)
    for verb in (("show", "A"), ("list"), ("board"), ("next")):
        run_cli(repo, *verb)
    assert _count(repo) == before
