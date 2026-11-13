"""B0b0b67d5e7: a finished gate outcome survives a transient contention on the events lock.

`record_measured` -> `hand_approve.record` appends under the append lock; when a lane
heartbeat holds that lock under load the append timed out once and the finished result was
lost. The record step retries the lock instead (each attempt the log's own timeout, bounded
by `[lease].record_lock_wait_s`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import item_of as _item

import ddflow.api.gates as AG
from ddflow.infra import fsio as fsio_mod


def test_a_finished_outcome_retries_a_contended_events_lock(repo, monkeypatch):
    """One timeout on the append lock is retried, not raised: the pass is recorded and the
    finished work is not lost."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")

    real = fsio_mod.file_lock
    timed_out = {"done": False}

    def contended(lock, timeout_s=None, **kw):
        # The acquire (``timeout_s`` is the log's lock timeout) must fail once; the
        # zero-timeout probe `_still_locked` uses (``timeout_s == 0``) must not be hit.
        if timeout_s and not timed_out["done"]:
            timed_out["done"] = True
            raise fsio_mod.LockTimeout(Path(lock), timeout_s)
        return real(lock, timeout_s, **kw)

    monkeypatch.setattr(fsio_mod, "file_lock", contended)

    # `research` is first in the pipeline, so the record is its ONLY append (nothing is
    # out of order yet); the contention therefore hits the outcome append itself.
    out = AG.record(repo, "T1", "research", outcome="passed", evidence=AG.Evidence(note="done"))
    assert out.exit == 0, out

    rec = _item(repo, "T1").gates["research"]
    assert rec.outcome == "passed", rec
    assert timed_out["done"], "the lock was never contended: the test did not exercise the retry"
