"""B86 + B6: the cache memory ceiling, and why the log is NOT compacted.

B86 filed "every state-reading call re-folds the whole log" and blamed the fold. The
measurement says otherwise: at 20,000 events a read costs 115 ms, of which `fold` is
9 ms (7%) and `Event.from_json` is 97 ms (84%). So the fix is not to shrink the log;
it is to stop re-parsing bytes that an append-only file guarantees have not changed.

B6 proposed the other direction: implement compaction, "keeping every PROVENANCE_KINDS
event plus the last state-bearing event per subject".
`test_the_filed_compaction_recipe_would_break_progress_and_loops` is the probe that killed
it. `ddflow progress` and `ddflow loops` read RAW events, not folded state, and leases and
gate outcomes are not provenance kinds, so that recipe silences a shipped detector. The
kind is gone; this file is the reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from log_cache_support import _cold_cache, _ids, _no_version_stamp, _uncached  # noqa: F401

from ddflow.config import Config, LogConfig
from ddflow.core import progress
from ddflow.core.events import PROVENANCE_KINDS
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog


def test_the_memory_ceiling_stops_the_cache_growing(repo):
    """~736 bytes per parsed event, so an unbounded cache is a long-lived server's leak."""
    from ddflow.infra import log as L

    log = EventLog(repo, "a1", log_cfg=LogConfig(max_cached_events=3))
    for i in range(3):
        log.append("task.added", f"T{i}", {"title": f"t{i}", "kind": "task"})
    log.read_all()
    assert L._PARSE_CACHE, "under the ceiling the cache should be used"

    log.append("task.added", "T4", {"title": "over", "kind": "task"})
    assert len({e.subject for e in log.read_all()}) == 4, "events lost at the ceiling"
    assert not L._PARSE_CACHE, (
        "over the ceiling the cache must be DROPPED, not left holding a stale prefix"
    )


def test_the_ceiling_counts_every_shard_not_each_one_separately(repo):
    """It is documented as a MEMORY ceiling, and a per-shard limit is not one.

    Found by the rubber-duck reviewer: the first version compared ONE shard's event count
    to the knob, so a repo with eight agents held eight times the promised ~74 MB, and a
    server serving several repos held a multiple of that again.
    """
    from ddflow.infra import log as L

    cfg = LogConfig(max_cached_events=6)
    for agent in ("a1", "a2", "a3", "a4"):
        log = EventLog(repo, agent, log_cfg=cfg)
        for i in range(3):
            log.append("task.added", f"{agent}-{i}", {"title": "x", "kind": "task"})
        log.read_all()
    assert L._cached_events() <= 6, (
        f"the cache holds {L._cached_events()} events against a ceiling of 6 — the ceiling "
        "is being applied per shard"
    )
    # ...and correctness is untouched by refusing to cache.
    assert len(EventLog(repo, "a1", log_cfg=cfg).read_all()) == 12
    assert len(_uncached(repo).read_all()) == 12


def test_going_over_the_ceiling_does_not_serve_a_stale_prefix(repo):
    """The subtle half: an entry left behind at the ceiling would be served forever."""
    log = EventLog(repo, "a1", log_cfg=LogConfig(max_cached_events=2))
    plain = _uncached(repo)
    for i in range(6):
        log.append("task.added", f"T{i}", {"title": f"t{i}", "kind": "task"})
        assert _ids(log.read_all()) == _ids(plain.read_all()), f"diverged at {i + 1} events"


# -- B6: why the kind is gone -------------------------------------------------------


def test_log_compacted_is_no_longer_a_declared_kind():
    assert "log.compacted" not in HANDLERS, (
        "the compaction marker is back; see this module's docstring for why it went"
    )


def test_the_filed_compaction_recipe_would_break_progress_and_loops(repo):
    """B6's own recipe, applied verbatim, silences a shipped loop detector.

    Quoting docs/BACKLOG.md: "rewrite shards keeping every PROVENANCE_KINDS event plus
    the last state-bearing event per subject". `progress.work` pairs `lease.acquired`
    with the next release over the WHOLE history, and leases are not provenance kinds —
    so the recipe keeps one of each per subject and the attempt history is gone.
    """
    cfg = Config.load(repo)
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "the task", "kind": "task"})
    for _ in range(6):
        log.append("lease.acquired", "T1", {"agent": "a1", "expires_at": 0})
        log.append("gate.started", "T1", {"gate": "tests"})
        log.append("gate.failed", "T1", {"gate": "tests"})
        log.append("lease.released", "T1", {"agent": "a1"})

    events = log.read_all()
    state = fold(events, strict=False)
    before = progress.detect(events, state, cfg)
    assert [f.kind for f in before] == ["repeat_claims"], (
        "the fixture no longer trips the detector, so it cannot show the loss"
    )
    assert len(progress.work(events, state)["T1"].attempts) == 6

    last: dict[tuple[str, str], int] = {}
    for i, ev in enumerate(events):
        if ev.kind not in PROVENANCE_KINDS:
            last[(ev.subject, ev.kind.split(".")[0])] = i
    keep = {i for i, ev in enumerate(events) if ev.kind in PROVENANCE_KINDS} | set(last.values())
    compacted = [ev for i, ev in enumerate(events) if i in keep]

    after_state = fold(compacted, strict=False)
    assert progress.detect(compacted, after_state, cfg) == [], (
        "if the recipe preserves the finding, compaction is implementable and B6 was "
        "closed for the wrong reason"
    )
    # ZERO, not "fewer". The recipe keeps the LAST lease event per subject, which here
    # is a `lease.released` with no `lease.acquired` to pair with — so the history does
    # not merely shrink, it becomes unreadable: six attempts, none of them recoverable.
    assert len(progress.work(compacted, after_state)["T1"].attempts) == 0
