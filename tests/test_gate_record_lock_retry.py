"""B0b0b67d5e7: a finished gate outcome survives a transient contention on the events lock.

`record_measured` -> `hand_approve.record` appends under the append lock; when a lane
heartbeat holds that lock under load the append timed out once and the finished result was
lost. The record step retries the lock instead, bounded by `[lease].record_lock_wait_s`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import item_of as _item

import ddflow.api.gates as AG
from ddflow.infra import fsio as fsio_mod
from ddflow.infra.log._lock import AppendLockTimeout


def _contend_once_the_events_lock(real, monkeypatch) -> dict:
    """A `file_lock` that times out the FIRST acquire of the events lock, then passes.
    The zero-timeout probe `_still_locked` uses (``timeout_s == 0``) is never contended."""
    timed_out = {"done": False}

    def contended(lock, timeout_s=None, **kw):
        if timeout_s and Path(lock).name == "events.lock" and not timed_out["done"]:
            timed_out["done"] = True
            raise fsio_mod.LockTimeout(Path(lock), timeout_s)
        return real(lock, timeout_s, **kw)

    monkeypatch.setattr(fsio_mod, "file_lock", contended)
    return timed_out


def test_a_finished_outcome_retries_a_contended_events_lock(repo, monkeypatch):
    """One timeout on the events append lock is retried, not raised: the pass is recorded
    and the finished work is not lost, and the contention hit the events lock itself."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")

    timed_out = _contend_once_the_events_lock(fsio_mod.file_lock, monkeypatch)

    # `research` is first in the pipeline, so the record is its ONLY append (nothing is
    # out of order yet); the contention therefore hits the outcome append itself.
    out = AG.record(repo, "T1", "research", outcome="passed", evidence=AG.Evidence(note="done"))
    assert out.exit == 0, out

    rec = _item(repo, "T1").gates["research"]
    assert rec.outcome == "passed", rec
    assert timed_out["done"], "the events lock was never contended: the retry was not exercised"


def test_record_lock_wait_s_zero_keeps_one_attempt_and_loses_the_outcome(repo, monkeypatch):
    """The knob bounds the retry: with `record_lock_wait_s = 0` one contended append still
    raises and the outcome is lost, exactly as before the fix."""
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "lease.record_lock_wait_s", "0")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")

    timed_out = _contend_once_the_events_lock(fsio_mod.file_lock, monkeypatch)

    with pytest.raises(AppendLockTimeout):
        AG.record(repo, "T1", "research", outcome="passed", evidence=AG.Evidence(note="done"))

    assert timed_out["done"]
    assert "research" not in _item(repo, "T1").gates
