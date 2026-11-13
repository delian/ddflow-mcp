"""A no-op heartbeat catch-up holds the append lock for a mark compare, not for a log read.

`_catch_up_globs` ran after every successful renewal and, for globs and again for
resources, did `with log.transaction(): fold(log.read_all())` before finding the lease
already matched the item. On a big log that is two full reads and folds under the only
append lock per heartbeat, and heartbeats run on a timer: the dominant lock holder, the
cause of the `events.lock` timeouts that lost finished gate outcomes (TX-heartbeat-catchup-lockfree).

The rule (docs/ddflow/lock-lifecycle.md): no read_all/fold under the lock unless the
state it was decided from has moved.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.infra.log._lock import _HELD, _held_key

HB = importlib.import_module(
    "ddflow.api.lifecycle.heartbeat"
)  # the module, not the re-exported function
OK = 0
TTL_S = 6


def _claimed(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "i/*")
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="alpha")
    assert code == OK, out + err


def _watch_catch_up(monkeypatch) -> dict[str, int]:
    """Count read_all calls made WHILE the append lock is held, inside `_catch_up_globs`."""
    seen = {"locked_reads": 0, "catch_ups": 0}
    inside = {"on": False}
    real_read = EventLog.read_all
    real_catch = HB._catch_up_globs

    def read_all(self, *a, **kw):
        if inside["on"] and _HELD.get(_held_key(self.lock_path), 0) >= 1:
            seen["locked_reads"] += 1
        return real_read(self, *a, **kw)

    def catch_up(*a, **kw):
        seen["catch_ups"] += 1
        inside["on"] = True
        try:
            return real_catch(*a, **kw)
        finally:
            inside["on"] = False

    monkeypatch.setattr(EventLog, "read_all", read_all)
    monkeypatch.setattr(HB, "_catch_up_globs", catch_up)
    return seen


def test_a_matching_live_lease_is_caught_up_without_reading_the_log_under_the_lock(
    repo, monkeypatch
):
    _claimed(repo)
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    seen = _watch_catch_up(monkeypatch)
    out = HB.heartbeat(repo, "T1", agent="alpha")
    assert out.exit == OK, out
    assert seen["catch_ups"] == 1  # the catch-up ran (the probe is live) ...
    assert seen["locked_reads"] == 0  # ... and read nothing under the lock


def test_a_mismatch_still_retargets_the_lease_and_rereads_under_the_lock(repo, monkeypatch):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ddflow"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "i/*")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", str(TTL_S))
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="alpha")[0] == OK
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    time.sleep(TTL_S + 1.5)  # lapsed: `update --globs` reaches the item, not the lease
    assert run_cli(repo, "update", "T1", "--globs", "i/*,j/*", agent="alpha")[0] == OK
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    seen = _watch_catch_up(monkeypatch)
    out = HB.heartbeat(repo, "T1", agent="alpha")
    assert out.exit == OK, out
    assert seen["locked_reads"] >= 1  # the mismatch re-reads inside the lock
    lease = fold(EventLog(repo, "alpha").read_all(), strict=False).items["T1"].lease
    assert sorted(lease.globs) == ["i/*", "j/*"]
