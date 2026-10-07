"""Replay equals live: what a running process sees equals a cold replay of the bytes on disk.

A long-lived reader (the MCP server, a hook) answers from the LIVE path: the parse cache,
the incrementally maintained Lamport order, the on-disk snapshot a restarted process
adopts, and the SQLite index `Store.ensure` rebuilds when the log moves. A REPLAY parses
every shard line from scratch, sorts by (lamport, agent, id), keeps the first of each id
and folds. The two must agree after any interleaving of appends from several agents, pulls
between clones (a shard copied in, out of Lamport order), and process restarts -- a
disagreement is an answer that depends on how long the process has been running.

Driven as a hypothesis state machine (B-uni-property-tests). The read path's own order is
pinned case by case in tests/test_log_merged_order.py; this pins the STATE built on it.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from evstrategies import payloads
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog, clear_parse_cache
from ddflow.infra.store import Store

#: Two clones that sync by copying shards, as `git pull` does: a1 and a2 work in A, a3 in B.
CLONES = {"A": ("a1", "a2"), "B": ("a3",)}


def replay(root: Path) -> list[Event]:
    """The unoptimised definition, independent of the code under test: parse every line of
    every shard, sort, keep the first of each id."""
    events: list[Event] = []
    for path in sorted((root / ".ddflow" / "events").glob("*.jsonl")):
        events.extend(Event.from_json(line) for line in path.read_text().splitlines() if line)
    seen: set[str] = set()
    out: list[Event] = []
    for e in sorted(events, key=Event.sort_key):
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out


class ReplayEqualsLive(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        clear_parse_cache()
        self.tmp = Path(tempfile.mkdtemp(prefix="ddflow-replay-"))
        self.roots = {c: self.tmp / c for c in CLONES}
        self.logs: dict[str, EventLog] = {}
        for clone, agents in CLONES.items():
            (self.roots[clone] / ".ddflow" / "events").mkdir(parents=True)
            for agent in agents:
                log = EventLog(self.roots[clone], agent)
                log.stamp = False  # the version stamp is not what is under test
                self.logs[agent] = log
        #: Every event each clone must hold: its own appends and what it pulled.
        self.expected: dict[str, set[str]] = {c: set() for c in CLONES}

    def teardown(self) -> None:
        clear_parse_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _clone_of(self, agent: str) -> str:
        return next(c for c, agents in CLONES.items() if agent in agents)

    @rule(agent=st.sampled_from([a for agents in CLONES.values() for a in agents]), p=payloads())
    def append(self, agent, p):
        kind, subject, data = p
        ev = self.logs[agent].append(kind, subject, data)
        self.expected[self._clone_of(agent)].add(ev.id)

    @rule(src=st.sampled_from(list(CLONES)), dst=st.sampled_from(list(CLONES)))
    def pull(self, src, dst):
        """Copy src's own shards into dst: dst gains events whose Lamport values may be
        BELOW what it has already read, which the incremental order must slot in."""
        if src == dst:
            return
        for agent in CLONES[src]:
            shard = self.roots[src] / ".ddflow" / "events" / f"{agent}.jsonl"
            if shard.exists():
                shutil.copyfile(shard, self.roots[dst] / ".ddflow" / "events" / shard.name)
        self.expected[dst] |= {e.id for e in replay(self.roots[src]) if e.agent in CLONES[src]}

    @rule()
    def restart(self):
        """A new process: nothing parsed in memory; the next read may adopt the snapshot."""
        clear_parse_cache()

    @precondition(lambda self: any(self.expected.values()))
    @rule(clone=st.sampled_from(list(CLONES)))
    def the_index_agrees(self, clone):
        """`Store.ensure` -- the state every command reads, rebuilding the SQLite index when
        the log moved -- equals the replay, and so do the index's item rows."""
        root = self.roots[clone]
        log = self.logs[CLONES[clone][0]]
        want = fold(replay(root), strict=False)
        store = Store(root)
        assert store.ensure(log) == want
        con = sqlite3.connect(store.path)
        try:
            rows = sorted(con.execute("select id, kind, state, title, parent from items"))
        finally:
            con.close()
        assert rows == sorted(
            (i.id, i.kind, i.state, i.title, i.parent) for i in want.items.values() if not i.removed
        )

    @invariant()
    def live_reads_equal_the_replay(self):
        for clone, agents in CLONES.items():
            cold = replay(self.roots[clone])
            assert {e.id for e in cold} == self.expected[clone]
            want = fold(cold, strict=False)
            for agent in agents:
                live = self.logs[agent].read_all()
                assert [e.id for e in live] == [e.id for e in cold]
                assert fold(live, strict=False) == want


ReplayEqualsLive.TestCase.settings = settings(
    max_examples=50, stateful_step_count=30, deadline=None, database=None
)
test_replay_equals_live = ReplayEqualsLive.TestCase
