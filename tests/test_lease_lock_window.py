"""B4: the lease decision folds OUTSIDE the append lock and proves currency inside it.

`acquire` and `_transition` used to hold the lock across `read_all()` + `fold()`. Folding
is cheap — 407k events/s measured — but the READ is not, and on the NFS mount this package
was designed against it costs ~36x its local price. Every other agent waiting to append
waits behind it.

The invariant is NOT "the fold happened inside the lock". It is **the state the decision is
made from reflects every event in the log at the moment of the append**. This file pins
that invariant directly, because it is the one thing the optimisation could break, and it
would break it silently: two agents would both read "free" and both claim, which is the
exact race the whole coordination layer exists to stop.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import leases as L


def _queue(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")


# -- the race the optimisation could reintroduce -------------------------------------------


def test_a_claim_landing_between_the_fold_and_the_lock_is_NOT_lost(repo, monkeypatch):
    """The whole point, stated as the failure it prevents.

    Agent A folds the log, finds T1 free, and reaches for the lock. Agent B claims T1
    first. A must refuse — not grant a second lease on the same item.

    Induced deterministically by appending B's claim from INSIDE A's `transaction()`, at
    the moment the lock is taken, which is precisely the window the fold moved out of.
    """
    _queue(repo)
    cfg = Config.load(repo)
    a_log = EventLog(repo, "agent-a")
    rival = EventLog(repo, "agent-b")

    import contextlib

    original = EventLog.transaction
    fired = {"n": 0}

    @contextlib.contextmanager
    def racing(self):
        with original(self):
            if fired["n"] == 0 and self is a_log:
                fired["n"] = 1
                # B's claim, appended directly: going through `acquire` would re-enter
                # the lock this context manager is already holding.
                rival.append(
                    "lease.acquired",
                    "T1",
                    {
                        "holder": "agent-b",
                        "at": time.time(),
                        "ttl_s": cfg.lease.ttl_s,
                        "globs": ["a.py"],
                        "worktree": "",
                        "branch": "",
                        "note": "",
                        "kind": "task",
                    },
                )
            yield self

    monkeypatch.setattr(EventLog, "transaction", racing)
    with pytest.raises(L.LeaseError) as exc:
        L.acquire(a_log, cfg, "T1", holder="agent-a")
    assert fired["n"] == 1, "the race was never induced, so this proves nothing"
    assert "agent-b" in str(exc.value), str(exc.value)

    monkeypatch.undo()
    leases = [
        json.loads(line)
        for shard in (repo / ".ddflow" / "events").glob("*.jsonl")
        for line in shard.read_text().splitlines()
        if line.strip() and json.loads(line)["kind"] == "lease.acquired"
    ]
    holders = {e["data"]["holder"] for e in leases if e["subject"] == "T1"}
    assert holders == {"agent-b"}, f"two agents hold T1: {holders}"


def test_a_release_landing_in_the_same_window_is_also_seen(repo, monkeypatch):
    """`_transition` took the same optimisation, so it needs the same proof.

    Here the concurrent event makes the operation SUCCEED where the stale state would
    have refused — the mirror of the case above, and the one a "re-check only if it makes
    us stricter" shortcut would get wrong.
    """
    _queue(repo)
    cfg = Config.load(repo)
    owner = EventLog(repo, "agent-a")
    rival = EventLog(repo, "agent-b")

    import contextlib

    original = EventLog.transaction
    fired = {"n": 0}

    @contextlib.contextmanager
    def racing(self):
        with original(self):
            if fired["n"] == 0 and self is owner:
                fired["n"] = 1
                rival.append(
                    "lease.acquired",
                    "T2",
                    {
                        "holder": "agent-a",
                        "at": time.time(),
                        "ttl_s": cfg.lease.ttl_s,
                        "globs": ["b.py"],
                        "worktree": "",
                        "branch": "",
                        "note": "",
                        "kind": "task",
                    },
                )
            yield self

    monkeypatch.setattr(EventLog, "transaction", racing)
    # T2 had NO lease when the fold happened; one appeared in the window. A renewal must
    # see it and succeed, rather than acting on the state it folded a moment earlier.
    assert L.renew(owner, "T2", holder="agent-a") is True
    assert fired["n"] == 1, "the race was never induced"


# -- the fingerprint itself ----------------------------------------------------------------


def test_the_mark_changes_on_every_kind_of_append(repo):
    """Sizes, not mtimes: mtime granularity is one second on some filesystems and two
    appends inside one second is exactly the case this has to catch. A new shard from
    another agent changes the KEY set, so that is caught too."""
    _queue(repo)
    log = EventLog(repo, "agent-a")

    # One append first, so this agent's own shard already exists and the next append
    # exercises GROWTH rather than creation — the two cases are checked separately below.
    log.append("item.blocked", "T1", {"reason": "create this agent's shard"})
    before = log.mark()
    assert before, "no shards at all, so the comparison below is vacuous"

    log.append("item.blocked", "T1", {"reason": "same shard grows"})
    grown = log.mark()
    assert grown != before, "a same-shard append did not change the fingerprint"
    assert [n for n, _s, _h in grown.shards] == [n for n, _s, _h in before.shards], (
        "expected the same shard to grow, not a new one"
    )

    EventLog(repo, "agent-b").append("item.blocked", "T2", {"reason": "new shard appears"})
    with_new = log.mark()
    assert {n for n, _s, _h in with_new.shards} > {n for n, _s, _h in grown.shards}, (
        "a new agent's first write must change the key set"
    )


def test_the_mark_is_taken_before_the_read_not_after():
    """The ordering IS the safety property, and it is invisible in the result.

    Fingerprint AFTER the read and a write landing in between is recorded in the mark, so
    the later comparison says "unchanged" while the folded state is missing that event.
    Before, the marks differ and the caller re-reads. Asserted on the source because the
    two orderings are indistinguishable from the outside until the day they are not.
    """
    import inspect

    src = inspect.getsource(EventLog.decide_then_append)
    assert src.index("self.mark(clock=False)") < src.index("decided = decide()"), (
        "the mark must be taken BEFORE the read; after it, a write in between is "
        "silently absorbed and the stale state is used to decide"
    )


def test_an_unchanged_log_is_not_re_read_under_the_lock(repo):
    """The optimisation, asserted rather than assumed.

    Without this, `decide_then_append` could re-decide every time and every test above
    would still pass -- the correctness tests cannot tell a fast path from a slow one,
    which is how an optimisation comes to be reverted by accident and nobody notices.
    """
    _queue(repo)
    log = EventLog(repo, "agent-a")
    reads = {"n": 0}

    def decide():
        reads["n"] += 1
        return object()

    with log.decide_then_append(decide):
        pass
    assert reads["n"] == 1, "the log was re-read even though nothing was appended"

    reads["n"] = 0

    def decide_and_race():
        reads["n"] += 1
        if reads["n"] == 1:
            EventLog(repo, "agent-b").append("item.blocked", "T1", {"reason": "raced"})
        return object()

    with log.decide_then_append(decide_and_race):
        pass
    assert reads["n"] == 2, "a log that changed must be decided from again, under the lock"


# -- B8: one body, not three ---------------------------------------------------------------


def test_renew_release_and_expire_all_go_through_one_body():
    """They were ~85% one body, differing in a guard and a payload.

    A refactor, so no behavioural probe is owed — but a RATCHET is worth having, because
    the three copies had already drifted once in whether they recorded the holder, and
    re-inlining one is the kind of change that looks local and is not: each is a
    read-then-write under a lock, and three copies is three chances for one to drift out
    of the lock.
    """
    import ast
    import inspect

    src = inspect.getsource(L)
    tree = ast.parse(src)
    bodies = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in ("renew", "release", "expire")
    }
    assert set(bodies) == {"renew", "release", "expire"}, sorted(bodies)

    for name, node in bodies.items():
        calls = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_transition" in calls, f"{name} no longer goes through _transition"
        attrs = {
            n.func.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "transaction" not in attrs, f"{name} opens its own transaction again"
        assert "append" not in attrs, f"{name} appends directly, bypassing the shared guard"
        assert "fold" not in calls, f"{name} folds the log itself again"
