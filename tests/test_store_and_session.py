import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard import session
from orchard.model import fold
from orchard.store import Store


@pytest.fixture
def store(repo, cfg):
    return Store(repo, cfg)


def test_dropping_the_db_changes_nothing(log, store):
    """The disposability contract: the index is a cache, never a record."""
    log.append("lesson.recorded", "L1", {"title": "Alpha rule", "rule": "do the alpha"})
    log.append("phase.added", "P1", {"title": "phase one"})
    before = store.rebuild(log)
    hits_before = store.search("lessons", "alpha", 3)
    store.path.unlink()
    after = store.rebuild(log)
    assert set(before.items) == set(after.items)
    assert store.search("lessons", "alpha", 3) == hits_before


def test_search_ranks_by_relevance_not_recency(log, store):
    for i, (t, r) in enumerate(
        [
            ("Cache invalidation", "clear the cache when the key changes"),
            ("Database migrations", "never drop a column in the same release"),
            ("Retry with backoff", "exponential backoff prevents a thundering herd"),
        ]
    ):
        log.append("lesson.recorded", f"L{i}", {"title": t, "rule": r})
    store.rebuild(log)
    assert store.search("lessons", "thundering herd backoff", 1)[0]["title"] == "Retry with backoff"
    assert store.search("lessons", "dropping a column", 1)[0]["title"] == "Database migrations"


def test_search_survives_hostile_query_text(log, store):
    log.append("lesson.recorded", "L1", {"title": "quotes", "rule": "x"})
    store.rebuild(log)
    for q in ['un"balanced', "NEAR(", "a OR", "-", "", "*", "col:val"]:
        store.search("lessons", q, 3)  # must not raise


def test_index_staleness_is_detected(log, store):
    store.rebuild(log)
    assert not store.stale(log)
    log.append("phase.added", "P9", {})
    assert store.stale(log)


# -- provenance -----------------------------------------------------------------------


def test_secrets_are_redacted_before_touching_disk(log, cfg):
    sid = session.start(log, cfg, model="m")
    session.prompt(
        log,
        cfg,
        sid,
        "deploy with api_key: sk-abcdefghijklmnop1234 and token=ghp_abcdefghijklmnopqrst",
    )
    raw = log.shard.read_text()
    assert "sk-abcdefghijklmnop1234" not in raw
    assert "ghp_abcdefghijklmnopqrst" not in raw
    assert "REDACTED" in raw


def test_redaction_keeps_the_surrounding_prompt(log, cfg):
    sid = session.start(log, cfg)
    session.prompt(log, cfg, sid, "Please build the login page. password: hunter2hunter2 thanks")
    text = fold(log.read_all()).sessions[sid].prompts[0]["text"]
    assert "build the login page" in text and "thanks" in text


def test_replay_carries_intent_and_drops_noise(log, cfg):
    sid = session.start(log, cfg, model="claude-opus-5")
    session.prompt(log, cfg, sid, "Build authentication")
    log.append("phase.added", "P1", {"title": "Auth"})
    log.append("task.added", "P1.T1", {"parent": "P1", "title": "login", "needs": []})
    log.append("lease.acquired", "P1.T1", {"holder": "a", "at": 1.0, "ttl_s": 60})
    log.append("gate.passed", "P1.T1", {"gate": "unit_tests", "evidence": {"exit": 0}})
    log.append(
        "research.recorded",
        "R1",
        {
            "question": "bcrypt or argon2?",
            "claim": "argon2id",
            "verdict": "CONFIRMED",
            "probe": "bench.py",
        },
    )
    log.append("item.completed", "P1.T1", {"sha": "deadbeef"})
    steps = session.replay(log.read_all())
    kinds = [s.kind for s in steps]
    assert "prompt" in kinds and "research" in kinds and "phase" in kinds
    assert "gate" not in kinds and "lease" not in kinds, "noise must not bury intent"
    assert any(s.sha == "deadbeef" for s in steps)


def test_reconstruction_brief_carries_what_a_rebuild_needs(log, cfg):
    sid = session.start(log, cfg)
    session.prompt(log, cfg, sid, "Build a URL shortener with a 6-char slug")
    log.append(
        "lesson.recorded",
        "L1",
        {"title": "Slugs must be collision-checked", "rule": "check before insert"},
    )
    log.append(
        "research.recorded",
        "R1",
        {
            "question": "base62 vs uuid",
            "claim": "uuid is fine",
            "verdict": "REFUTED",
            "falsifier": "measured 36-char urls",
            "probe": "p.py",
        },
    )
    st = fold(log.read_all())
    doc = session.render_reconstruction(st, session.replay(log.read_all()), project="shortener")
    assert "URL shortener with a 6-char slug" in doc
    assert "Slugs must be collision-checked" in doc
    assert "already tried and rejected" in doc and "uuid is fine" in doc
    # The brief must state its own limit: it reproduces the DECISIONS, not the bytes.
    # A reader who mistakes a provenance record for a promise of byte-identity will
    # trust a rebuild that silently differs.
    flat = " ".join(doc.split())
    assert "will not reproduce the original source byte-for-byte" in flat
    assert "model outputs are not deterministic" in flat


def test_bundle_is_self_contained(log, cfg, repo, tmp_path):
    sid = session.start(log, cfg)
    session.prompt(log, cfg, sid, "do the thing")
    log.append("phase.added", "P1", {"title": "t"})
    st = fold(log.read_all())
    log.append(
        "research.recorded",
        "R1",
        {
            "question": "q",
            "claim": "c",
            "verdict": "REFUTED",
            "probe": "p.sh",
            "probe_output": "measured: no",
        },
    )
    st = fold(log.read_all())
    files = session.bundle(st, session.replay(log.read_all()), tmp_path / "kit")
    names = {f.name for f in files}
    # RESEARCH.md is in the set deliberately: the bundle and `render.write_views` were
    # two copies of one generator map and had already drifted by exactly this file, so
    # a recovery kit omitted the rejected-approaches log — the part a rebuild most needs.
    assert names == {"RECONSTRUCTION.md", "QUEUE.md", "LESSONS.md", "RESEARCH.md"}
    assert all(f.read_text().strip() for f in files)
    assert "REFUTED" in (tmp_path / "kit" / "RESEARCH.md").read_text()


def test_a_rejected_approach_carries_the_measurement_that_killed_it(log, cfg):
    """A rejection without its evidence invites re-litigation.

    The brief's whole value here is stopping a future session re-researching something
    already settled — and "we rejected X" is not settled, while "we rejected X, here is
    the measurement" is. Found by the reconstruct-from-log demo scenario.
    """
    log.append(
        "research.recorded",
        "R1",
        {
            "question": "denormalised running balance?",
            "claim": "it is worth the risk",
            "verdict": "REFUTED",
            "probe": "python3 probe_concurrent_balance.py",
            "probe_output": "12 writers: 3 inconsistent balances out of 500 txns",
        },
    )
    st = fold(log.read_all())
    doc = session.render_reconstruction(st, session.replay(log.read_all()))
    assert "3 inconsistent balances out of 500" in doc, "the measurement was dropped"
    assert "probe_concurrent_balance.py" in doc


def test_replay_carries_the_phase_and_task_BODIES(log, cfg):
    """The body holds the acceptance criteria and the context — the part a rebuild most
    needs. Emitting only the title reduced a phase to a label: "P1: Core" says nothing
    about what Core has to do. Found by the end-to-end orchestration scenario.
    """
    log.append(
        "phase.added",
        "P1",
        {"title": "Core", "body": "Duration parsing, statistics, and a CLI over both."},
    )
    log.append(
        "task.added",
        "P1.T1",
        {
            "parent": "P1",
            "title": "parser",
            "globs": ["src/parse.py"],
            "body": "Must reject any input not fully consumed by the token pattern.",
        },
    )
    st = fold(log.read_all())
    doc = session.render_reconstruction(st, session.replay(log.read_all()))
    assert "Duration parsing, statistics, and a CLI over both." in doc
    assert "not fully consumed by the token pattern" in doc
    assert "writes src/parse.py" in doc, "the declared file scope is part of the plan"
