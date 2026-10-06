"""B86 + B6: reading the append-only log without re-parsing it, and why NOT compaction.

B86 filed "every state-reading call re-folds the whole log" and blamed the fold. The
measurement says otherwise: at 20,000 events a read costs 115 ms, of which `fold` is
9 ms (7%) and `Event.from_json` is 97 ms (84%). So the fix is not to shrink the log —
it is to stop re-parsing bytes that an append-only file guarantees have not changed.

B6 proposed the other direction: implement compaction, "keeping every PROVENANCE_KINDS
event plus the last state-bearing event per subject". `test_the_filed_compaction_recipe_would_break_progress_and_loops`
is the probe that killed it. `ddflow progress` and `ddflow loops` read RAW events, not
folded state, and leases and gate outcomes are not provenance kinds — so that recipe
silences a shipped detector. The kind is gone; this file is the reason.
"""

from __future__ import annotations

import ast
import json
import subprocess

import pytest

from ddflow.config import Config, LogConfig
from ddflow.core import progress
from ddflow.core.events import PROVENANCE_KINDS
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog, clear_parse_cache


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    """These tests are about the log's storage mechanics (event counts, byte offsets, clock
    values); the version stamp (B-upgrade.1-stamp) is tested in tests/test_upgrade_stamp.py."""
    monkeypatch.setattr(EventLog, "stamp", False)


@pytest.fixture(autouse=True)
def _cold_cache():
    """Every test starts with nothing parsed. A cache shared between tests is a test
    that passes because of its neighbour."""
    clear_parse_cache()
    yield
    clear_parse_cache()


def _uncached(repo, agent="a1") -> EventLog:
    return EventLog(repo, agent, log_cfg=LogConfig(reuse_parsed=False))


def _ids(events) -> list[str]:
    return [e.id or e.compute_id() for e in events]


# -- B86: the incremental read ------------------------------------------------------


def test_a_cached_read_returns_exactly_what_an_uncached_one_does(repo):
    log, plain = EventLog(repo, "a1"), _uncached(repo)
    for i in range(20):
        log.append("task.added", f"T{i}", {"title": f"task {i}", "kind": "task"})
    assert _ids(log.read_all()) == _ids(plain.read_all())


def test_an_event_appended_after_the_cache_warmed_is_still_read(repo):
    """The bug this design could plausibly have: a warm cache serving a stale answer."""
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "first", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"T1"}  # warms the cache

    log.append("task.added", "T2", {"title": "second", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"T1", "T2"}, (
        "the append landed after the cache warmed and was not picked up"
    )


def test_a_second_agents_new_shard_is_picked_up(repo):
    """A new shard changes the KEY set, not any cached shard's size."""
    a, b = EventLog(repo, "a1"), EventLog(repo, "a2")
    a.append("task.added", "T1", {"title": "mine", "kind": "task"})
    reader = EventLog(repo, "a1")
    assert {e.subject for e in reader.read_all()} == {"T1"}
    b.append("task.added", "T2", {"title": "theirs", "kind": "task"})
    assert {e.subject for e in reader.read_all()} == {"T1", "T2"}


def test_a_torn_tail_is_reported_once_and_read_once_it_is_completed(repo):
    """The torn-tail resume class, which is how this design fails if it is wrong.

    A crash mid-append leaves a fragment with no newline. Marking it consumed would
    skip the event FOREVER once the writer finished the line — silently, because the
    fold has no way to know a line was dropped.
    """
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "whole", "kind": "task"})
    log.read_all()  # warm

    head = b'{"kind":"task.added","subject":"TORN","data":{"title":"t","kind":"task"}'
    with log.shard.open("ab") as fh:
        fh.write(head)
    events = log.read_all()
    assert log.skipped_lines == 1, "a torn fragment must be reported, not hidden"
    assert "TORN" not in {e.subject for e in events}

    tail = b',"lamport":999,"ts":"2026-09-27T00:00:00Z","agent":"a1"}\n'
    with log.shard.open("ab") as fh:
        fh.write(tail)
    events = log.read_all()
    assert "TORN" in {e.subject for e in events}, (
        "the completed line was marked consumed while torn and is now lost forever"
    )
    assert log.skipped_lines == 0


def test_a_shard_rewritten_in_place_is_re_parsed(repo):
    """The defect the first version of this shipped, found by the rubber-duck reviewer.

    The cache used to validate on `st_ino`, documented as "a `git merge` writes a temp
    file and renames, so the inode changes". That is false — probed directly:

        $ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl -> 218500670
        $ git checkout -q main  && stat -c %i .ddflow/events/a1.jsonl -> 218500670

    `open(path, "w")` likewise truncates and KEEPS the inode. So any rewrite that did not
    shrink the file defeated both checks: the prefix came from the cache, the tail read
    started mid-line in the new content, and the result mixed events that were gone with
    a bogus torn-append report.
    """
    log = EventLog(repo, "a1")
    log.append("task.added", "OLD1", {"title": "x", "kind": "task"})
    log.append("task.added", "OLD2", {"title": "y", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"OLD1", "OLD2"}  # warm

    # Rewritten IN PLACE, same inode, LONGER than before -- so neither an inode check
    # nor a shrink check can see it.
    fresh = _uncached(repo)
    other = EventLog(repo.parent / "scratch", "a1")
    lines = []
    for n in ("NEW1", "NEW2", "NEW3"):
        lines.append(
            json.dumps(
                {
                    "kind": "task.added",
                    "subject": n,
                    "data": {"title": n, "kind": "task"},
                    "agent": "a1",
                    "lamport": 1,
                    "ts": "2026-09-27T00:00:00Z",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    del other
    before_ino = log.shard.stat().st_ino
    with log.shard.open("w") as fh:  # truncate + write: the inode does NOT change
        fh.write("\n".join(lines) + "\n")
    assert log.shard.stat().st_ino == before_ino, "the fixture replaced the file; retry"
    assert log.shard.stat().st_size > 0

    got = {e.subject for e in log.read_all()}
    assert got == {"NEW1", "NEW2", "NEW3"}, f"stale events served from the cache: {got}"
    assert got == {e.subject for e in fresh.read_all()}
    assert log.skipped_lines == 0, "a mid-line tail read invented a torn append"


def test_a_git_checkout_between_DIVERGENT_branches_is_re_parsed(repo):
    """The real-world trigger, with git doing the writing — and it has to DIVERGE.

    A long-lived MCP server hits this the moment the operator switches branches.
    `Store.rebuild` would then persist the wrong events into SQLite stamped with the
    CORRECT `head()` fingerprint, so a fresh process would not rebuild them away: a
    transient cache bug becomes on-disk corruption.

    Two earlier versions of this test were VACUOUS, each for its own reason, and both are
    why the assertions below are so specific:

      1. Warming on the LONGER branch and checking out the shorter one tripped the shrink
         check that existed for another purpose.
      2. Warming on a branch the other had merely APPENDED to made one shard a literal
         prefix of the other — so reading the tail from the cached offset gives the right
         answer even with no validity check at all.

    So the branches must diverge from a shared base: the cached prefix then disagrees with
    the file's actual bytes at that offset, which is the condition a digest catches and a
    stat cannot.
    """
    sh = ["git", "-C", str(repo)]
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]

    def commit(msg):
        subprocess.run([*sh, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*sh, *ident, "commit", "-qm", msg], check=True, capture_output=True)

    log = EventLog(repo, "a1")
    log.append("task.added", "BASE", {"title": "shared history", "kind": "task"})
    commit("base")

    subprocess.run([*sh, "checkout", "-q", "-b", "short"], check=True, capture_output=True)
    log.append("task.added", "SHORT1", {"title": "only on short", "kind": "task"})
    commit("short")

    subprocess.run([*sh, "checkout", "-q", "main"], check=True, capture_output=True)
    subprocess.run([*sh, "checkout", "-q", "-b", "long"], check=True, capture_output=True)
    for n in ("LONG1", "LONG2", "LONG3"):
        log.append("task.added", n, {"title": "only on long", "kind": "task"})
    commit("long")

    subprocess.run([*sh, "checkout", "-q", "short"], check=True, capture_output=True)
    small = log.shard.stat()
    assert {e.subject for e in log.read_all()} == {"BASE", "SHORT1"}  # warms the cache

    subprocess.run([*sh, "checkout", "-q", "long"], check=True, capture_output=True)
    big = log.shard.stat()
    assert big.st_size > small.st_size, "fixture: the shard must GROW or a shrink check hides it"
    # Git REPLACES the file on checkout. The inode number usually comes back unchanged only
    # because the filesystem hands the just-freed number straight back -- the reuse that
    # fooled the old inode-keyed cache, and what lets this test tell a digest from a stat.
    # A concurrent process (a parallel test worker) can take that number first; this used
    # to FAIL the precondition under `pytest -n auto` (bug Befd838f4da). The re-parse below
    # must be right either way: a new inode costs one run its discriminating power, not
    # its correctness check.
    # The branches genuinely DIVERGE: the cached offset now lands mid-line, which is the
    # condition a digest catches and no stat can. Asserted, not assumed -- if `long` ever
    # became an append to `short`, this test would silently go back to proving nothing.
    assert b"SHORT1" not in log.shard.read_bytes(), "the branches did not diverge"
    assert log.shard.read_bytes()[small.st_size - 1 : small.st_size] != b"\n", (
        "the cached offset happens to land on a line boundary, so the stale prefix would "
        "parse cleanly and the defect would not show"
    )

    got = {e.subject for e in log.read_all()}
    assert got == {"BASE", "LONG1", "LONG2", "LONG3"}, (
        f"a warm cache served the branch we left: {got}"
    )
    assert got == {e.subject for e in _uncached(repo).read_all()}
    assert log.skipped_lines == 0, "a mid-line tail read invented a torn append"


def test_a_shard_deleted_and_recreated_on_the_same_path_is_re_parsed(repo):
    """`rm -rf .ddflow && ddflow init` under a running server. ext4 hands the freed inode
    straight back, so the recreated file can land on the same inode AND the same path."""
    log = EventLog(repo, "a1")
    log.append("task.added", "OLD", {"title": "x", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"OLD"}  # warm

    log.shard.unlink()
    assert log.read_all() == [], "a deleted shard's events are still being served"

    log.append("task.added", "NEW", {"title": "y", "kind": "task"})
    assert {e.subject for e in log.read_all()} == {"NEW"}


def test_an_unreadable_shard_raises_rather_than_vanishing(repo):
    """A shard that is THERE and cannot be read must not be silently dropped.

    The pre-cache code caught `FileNotFoundError` only. Widening that to `OSError` — which
    the first version of this change did — turned a loud failure into a whole agent's
    events disappearing from the queue while `read_all` reported success and
    `skipped_lines` stayed 0, so nothing downstream could tell. Found by the cross-family
    critic. A file that has genuinely VANISHED is still benign and still skipped.
    """
    import os

    mine, theirs = EventLog(repo, "a1"), EventLog(repo, "a2")
    mine.append("task.added", "MINE", {"title": "x", "kind": "task"})
    theirs.append("task.added", "THEIRS", {"title": "y", "kind": "task"})
    clear_parse_cache()
    assert {e.subject for e in mine.read_all()} == {"MINE", "THEIRS"}

    os.chmod(theirs.shard, 0o000)
    clear_parse_cache()
    try:
        if os.geteuid() == 0:
            pytest.skip("running as root: the permission bit does not bite")
        with pytest.raises(PermissionError):
            mine.read_all()
    finally:
        os.chmod(theirs.shard, 0o644)

    # A vanished shard, by contrast, is benign and skipped.
    theirs.shard.unlink()
    clear_parse_cache()
    assert {e.subject for e in mine.read_all()} == {"MINE"}


def test_a_vanished_shards_cache_entry_is_evicted(repo):
    """Otherwise the entry lives for the process lifetime: nothing visits a path that
    `shards()` no longer returns, so `_read_shard` never sees the deletion."""
    from ddflow.infra import log as L

    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "x", "kind": "task"})
    log.read_all()
    shard = log.shard
    assert shard in L._PARSE_CACHE

    shard.unlink()
    log.read_all()
    assert shard not in L._PARSE_CACHE, "a deleted shard is still held in the cache"


def test_a_change_early_in_a_long_prefix_is_detected(repo):
    """The digest must cover the WHOLE consumed prefix, not a trailing window of it.

    A window is the cheap version of this check and it is unsound: a divergence entirely
    before the window, with identical bytes after it, is invisible. Mutation N2b — a
    512-byte trailing window — passed every other test here, because their fixtures are
    smaller than 512 bytes. This one is not.
    """
    log = EventLog(repo, "a1")
    for i in range(40):  # comfortably past any plausible window size
        log.append(
            "task.added", f"T{i:03d}", {"title": "padding to exceed a window", "kind": "task"}
        )
    assert len(log.read_all()) == 40  # warms the cache
    raw = log.shard.read_bytes()
    assert len(raw) > 4096, "fixture too small to distinguish a window from the whole prefix"

    # Change the FIRST event only, same byte length, so every later byte is identical.
    assert raw.count(b'"T000"') == 1
    rewritten = raw.replace(b'"T000"', b'"T999"', 1)
    assert len(rewritten) == len(raw), "the edit changed the length; a size check would see it"
    assert rewritten[-512:] == raw[-512:], "the edit reached the tail; it must not"
    with log.shard.open("wb") as fh:  # in place: same inode, same size
        fh.write(rewritten)

    subjects = {e.subject for e in log.read_all()}
    assert "T999" in subjects and "T000" not in subjects, (
        "a change early in the prefix was not detected — the validity check is only "
        f"looking at part of it. Got: {sorted(subjects)[:3]}..."
    )


def test_a_truncated_shard_is_re_parsed(repo):
    log = EventLog(repo, "a1")
    for i in range(5):
        log.append("task.added", f"T{i}", {"title": f"t{i}", "kind": "task"})
    assert len(log.read_all()) == 5
    lines = log.shard.read_text().splitlines()
    log.shard.write_text("\n".join(lines[:2]) + "\n")
    assert len(log.read_all()) == 2, "a truncated shard kept serving the events it lost"


def test_reuse_parsed_false_is_honoured_and_reaches_the_real_read_path(repo):
    """The dead-knob class: a knob that loads and changes nothing.

    Asserts against the CACHE, not against a return value — both settings return the
    same events, which is exactly why a parity assertion would pass either way.
    """
    from ddflow.infra import log as L

    off = _uncached(repo)
    off.append("task.added", "T1", {"title": "x", "kind": "task"})
    off.read_all()
    assert not L._PARSE_CACHE, "reuse_parsed=false still populated the cache"

    on = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=True))
    on.read_all()
    assert L._PARSE_CACHE, "reuse_parsed=true did not populate the cache"


def test_reuse_parsed_false_does_not_read_a_cache_another_reader_filled(repo):
    """The other half of the knob, and the one a parity assertion cannot see.

    Guarding only the WRITE side leaves a disabled reader still consulting an entry that
    an enabled one put there — so `reuse_parsed = false` would not actually disable the
    cache. Found by mutation M5: removing the read guard left every knob test green.

    Poisons the cache deliberately. The first assertion is the control arm proving the
    poison bites; without it this test could pass because the poison did nothing.
    """
    from ddflow.infra import log as L

    on = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=True))
    on.append("task.added", "T1", {"title": "x", "kind": "task"})
    assert {e.subject for e in on.read_all()} == {"T1"}

    entry = L._PARSE_CACHE[on.shard]
    # Keeps the DIGEST, so the validity check passes and the (empty) events are trusted.
    L._PARSE_CACHE[on.shard] = L._Parsed(entry.consumed, entry.digest, [], 0)
    assert on.read_all() == [], "the poison did not bite, so this test proves nothing"

    off = _uncached(repo)
    assert {e.subject for e in off.read_all()} == {"T1"}, (
        "reuse_parsed=false read from the cache anyway"
    )


def test_the_config_knob_is_honoured_by_the_real_entry_points(repo):
    """Drives the REAL commands, because the hand-built version proved nothing.

    The first version of this test was named for the real wiring and then built the log
    by hand — so it passed while three shipped call sites (`api.loops`, `api.progress`,
    the MCP obligation footer) constructed `EventLog(repo)` with no `log_cfg` and ignored
    `[log] reuse_parsed = false` entirely. Found by roborev on 8b3ce8f, after a subagent
    reviewer and the cross-family critic both missed it: a test that asserts the thing it
    is named for is the only thing that would have caught it.
    """
    from ddflow.api import reporting
    from ddflow.infra import log as L

    cfgpath = repo / ".ddflow" / "config.toml"
    cfgpath.parent.mkdir(parents=True, exist_ok=True)
    cfgpath.write_text("[log]\nreuse_parsed = false\n")
    cfg = Config.load(repo)
    assert cfg.log.reuse_parsed is False
    assert cfg.sources["log.reuse_parsed"] == "file"

    seed = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=False))
    seed.append("task.added", "T1", {"title": "x", "kind": "task"})

    def _footer() -> None:
        """The MCP obligation footer, the third shipped site that built a bare EventLog.

        Added because roborev mutation-tested the commit that fixed it and found only the
        AST ratchet went red — so the commit message's claim that "both the behavioural
        test and the ratchet go red" was true for the two API paths and false for this one.
        A static guard cannot see a `log_cfg` threaded to the wrong object.
        """
        from ddflow.surfaces import mcp as M

        server = M.Server(repo)
        server._calls_since_footer = 10_000
        server._last_footer_at = 0.0
        M._obligation_footer(server)

    for label, call in (
        ("api.loops", lambda: reporting.loops(repo)),
        ("api.progress", lambda: reporting.progress(repo)),
        ("mcp obligation footer", _footer),
    ):
        clear_parse_cache()
        call()
        assert not L._PARSE_CACHE, f"{label} ignored [log] reuse_parsed = false"


def event_log_calls_without_config(src: str) -> list[int]:
    """Line numbers of `EventLog(...)` constructions in ``src`` that omit `log_cfg`.

    Injectable so the detector can be SHOWN to work. A scan whose only input is the live
    tree passes identically once it has silently stopped looking — which is exactly what
    happened here: the first version matched only a bare `ast.Name`, so mutating the name
    it compares against changed nothing, because no qualified call existed to miss.
    """
    import ast

    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        # `EventLog(...)` and `L.EventLog(...)` alike. roborev found the Attribute form
        # unguarded on 844bad2 while the docstring promised "every call site".
        func = node.func
        if isinstance(func, ast.Name):
            called = func.id
        elif isinstance(func, ast.Attribute):
            called = func.attr
        else:
            continue
        if called != "EventLog":
            continue
        if any(k.arg == "log_cfg" for k in node.keywords):
            continue
        out.append(node.lineno)
    return out


@pytest.mark.parametrize(
    ("src", "offends"),
    [
        ("EventLog(repo)", True),
        ("L.EventLog(repo)", True),
        ("infra_log.EventLog(repo, agent)", True),
        ('EventLog(repo, "a1", lock_timeout_s=5)', True),
        ("EventLog(repo, log_cfg=cfg.log)", False),
        ("L.EventLog(repo, log_cfg=cfg.log)", False),
        ('EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=False))', False),
        ("SomethingElse(repo)", False),
    ],
)
def test_the_call_site_detector_sees_both_call_forms(src, offends):
    assert bool(event_log_calls_without_config(src)) is offends, src


def test_every_event_log_call_site_passes_the_log_config():
    """A ratchet, because threading a knob through N call sites is how knobs get dropped.

    Three of the eight sites were missed on the first pass and the behavioural test could
    not see them. This one names the offender directly, and the allowlist may only shrink.
    """
    import pathlib as _p

    #: Call sites that legitimately take no `log_cfg`, each with its reason.
    allowed: dict[str, str] = {}

    pkg = _p.Path(__file__).resolve().parents[1] / "ddflow"
    offenders = []
    for f in sorted(pkg.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        for line in event_log_calls_without_config(f.read_text("utf-8")):
            where = f"{f.relative_to(pkg.parent)}:{line}"
            if where not in allowed:
                offenders.append(where)
    assert not offenders, (
        "these EventLog call sites drop `[log]` config, so `reuse_parsed` and "
        f"`max_cached_events` silently do nothing there: {offenders}"
    )


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


# -- the invariant the cache rests on ------------------------------------------------


#: Receiver names whose `.data` is NOT an event's. `Outcome`, in every case.
_ALLOWED_DATA_RECEIVERS = {"out"}
#: dict methods that mutate in place. `setdefault` is here because it WRITES on a miss,
#: which reads like a lookup at the call site.
_DICT_MUTATORS = frozenset({"update", "pop", "popitem", "setdefault", "clear"})


def _data_receiver(node: ast.AST) -> str | None:
    """The name whose `.data` this expression touches, or None.

    Returns the unparsed receiver, so `self.ev.data` and `ev.data` are both reported —
    an attribute receiver is just as capable of holding a cached event.
    """
    if isinstance(node, ast.Attribute) and node.attr == "data":
        base = node.value
        return base.id if isinstance(base, ast.Name) else ast.unparse(base)
    return None


def _write_targets(node: ast.AST) -> list[ast.expr]:
    """What this statement writes to: `x = `, `x += `, `x: T = ` and `del x` alike.

    `AnnAssign` is here because `ev.data['k']: int = 1` is a write that the first version
    of this walked straight past, and `AugAssign` because `ev.data |= {...}` is PEP 584's
    in-place dict merge — the standard way to do the thing this ratchet forbids. Both
    found by the cross-family critic.
    """
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    return []


def _scope_body(scope: ast.AST) -> list[ast.AST]:
    """Every node lexically inside `scope`, NOT descending into a nested function or class.

    `ast.walk` descends into everything, which made an alias bound in one function visible
    in every other: `def a(ev): d = ev.data` registered `d` at module level, and an
    unrelated `d = {}; d['k'] = 1` in a sibling function was then reported as mutating an
    event. It over-fired rather than under-fired, so it would have shown up as a spurious
    red build rather than a miss — but a ratchet that cries wolf gets switched off, and
    the docstring said "in the same scope" while the code did not.
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue  # its own scope; visited separately
        stack.extend(ast.iter_child_nodes(node))
    return out


def _direct_data_writes(tree: ast.AST, allowed: set[str]) -> list[str]:
    """Writes straight through the attribute: `ev.data[k] = v`, `ev.data |= {...}`,
    `del ev.data[k]`, `ev.data.update(...)`."""
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        for t in _write_targets(node):
            # `ev.data[k] = v` -- the subscript's receiver is the `.data` attribute.
            if isinstance(t, ast.Subscript):
                r = _data_receiver(t.value)
                if r and r not in allowed:
                    out.append(f"{line}: {r}.data[...] written")
            # `ev.data |= {...}` / `ev.data = ...` -- the TARGET is the attribute itself.
            else:
                r = _data_receiver(t)
                if r and r not in allowed:
                    out.append(f"{line}: {r}.data written whole")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _DICT_MUTATORS
        ):
            r = _data_receiver(node.func.value)
            if r and r not in allowed:
                out.append(f"{line}: {r}.data.{node.func.attr}()")
    return out


def _aliased_data_writes(tree: ast.AST, allowed: set[str]) -> list[str]:
    """`d = ev.data` and then `d[k] = v` in the SAME scope.

    Aliasing to READ is everywhere and fine — `d = ev.data; d.get(...)`, 14 sites — so the
    alias alone is not the offence. Mutating through it is, and it is the route that
    escapes a direct-write scan entirely. Attribute aliases (`self.d = ev.data`) count too.
    """
    out = []
    scopes: list[ast.AST] = [tree]
    scopes += [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
    ]
    for scope in scopes:
        nodes = _scope_body(scope)
        aliases: dict[str, int] = {}
        for n in nodes:
            if not isinstance(n, ast.Assign):
                continue
            r = _data_receiver(n.value)
            if not r or r in allowed:
                continue
            for t in n.targets:
                if isinstance(t, ast.Name | ast.Attribute):
                    aliases[ast.unparse(t)] = n.lineno
        if not aliases:
            continue
        for n in nodes:
            line = getattr(n, "lineno", 0)
            for t in _write_targets(n):
                if isinstance(t, ast.Subscript) and ast.unparse(t.value) in aliases:
                    name = ast.unparse(t.value)
                    out.append(f"{line}: {name}[...] written; aliases event data")
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in _DICT_MUTATORS
                and ast.unparse(n.func.value) in aliases
            ):
                name = ast.unparse(n.func.value)
                out.append(f"{line}: {name}.{n.func.attr}(); aliases event data")
    return out


def data_writes(src: str, allowed: set[str] | None = None) -> list[str]:
    """Every route by which `src` could mutate an event's `data` dict.

    "Every" is a claim the parametrised test below keeps honest: three of these routes
    were added after a reviewer showed the previous version walked past them.
    """
    allowed = _ALLOWED_DATA_RECEIVERS if allowed is None else allowed
    tree = ast.parse(src)
    return _direct_data_writes(tree, allowed) + _aliased_data_writes(tree, allowed)


@pytest.mark.parametrize(
    "planted",
    [
        "ev.data['k'] = 1",
        "ev.data['k'] += 1",
        "del ev.data['k']",
        "ev.data.update({'k': 1})",
        "ev.data.setdefault('k', 1)",
        "ev.data.pop('k')",
        "ev.data.clear()",
        "def f(ev):\n    d = ev.data\n    d['k'] = 1\n",
        "def f(ev):\n    d = ev.data\n    d.update({'k': 1})\n",
        "def f(ev):\n    d = ev.data\n    del d['k']\n",
        # The three the cross-family critic found the first version walking past.
        "ev.data |= {'k': 1}",  # PEP 584 in-place dict merge
        "ev.data['k']: int = 1",  # annotated subscript assignment
        "def f(self, ev):\n    self.d = ev.data\n    self.d['k'] = 1\n",  # attribute alias
        "def f(self, ev):\n    self.d = ev.data\n    self.d.update({'k': 1})\n",
        # An attribute RECEIVER, not an attribute alias: the event itself is held on an
        # object. `_data_receiver` must unparse it rather than give up on non-Name bases.
        "self.ev.data['k'] = 1",
        "self.ev.data.update({'k': 1})",
        "rec(item).ev.data['k'] = 1",
    ],
)
def test_the_data_write_detector_catches_every_route(planted):
    """The detector is fed planted input, so it can be SHOWN to work.

    A scan whose only input is the live tree passes identically when it has silently
    stopped looking — the failure mode `test_ratchet_no_dead_knobs` names, and the reason
    the first version of this ratchet missed three of these ten routes.
    """
    assert data_writes(planted), f"the detector walked past: {planted!r}"


def test_reading_an_events_data_is_not_flagged():
    """The other half: a ratchet that fires on `d.get(...)` would be turned off."""
    assert not data_writes("def f(ev):\n    d = ev.data\n    return d.get('k'), ev.data['j']\n")
    assert not data_writes("out.data['text'] = 'allowlisted: this is an Outcome'")


def test_an_alias_in_one_function_does_not_leak_into_another(repo):
    """The over-fire the cross-family critic found: `ast.walk` descends into every nested
    body, so an alias bound in one function registered at module level and an unrelated
    local of the same name in a sibling was reported as mutating an event.

    It fails in the SAFE direction — a spurious red rather than a miss — but a ratchet
    that cries wolf gets switched off, and `_aliased_data_writes` said "in the same scope"
    while doing nothing of the kind.
    """
    leaky = "def a(ev):\n    d = ev.data\n    return d\ndef b():\n    d = {}\n    d['k'] = 1\n"
    assert not data_writes(leaky), (
        "an unrelated local that merely shares a name with an event-data alias was flagged"
    )
    # ...while a mutation in the scope that DOES hold the alias is still caught.
    real = "def a(ev):\n    d = ev.data\n    d['k'] = 1\n"
    assert data_writes(real), "narrowing the scope broke the thing it is here to catch"


def test_nothing_in_the_package_mutates_an_events_data_dict():
    """`Event` is a frozen dataclass whose `data` dict is NOT frozen.

    Before the cache, every `read_all()` built fresh `Event` objects, so mutating one was
    invisible. They are now shared across every read in the process, and a single
    `ev.data[k] = v` anywhere would poison every later caller — a corruption that surfaces
    in an unrelated command, long after the line that caused it.

    A ratchet, not a style rule: `_ALLOWED_DATA_RECEIVERS` may only shrink.
    """
    import pathlib as _p

    pkg = _p.Path(__file__).resolve().parents[1] / "ddflow"
    offenders = []
    for f in sorted(pkg.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        offenders += [
            f"{f.relative_to(pkg.parent)}:{hit}" for hit in data_writes(f.read_text("utf-8"))
        ]
    assert not offenders, (
        "a shared, cached Event's data dict is being mutated (or a new Outcome receiver "
        f"needs allowlisting with its reason): {offenders}"
    )
