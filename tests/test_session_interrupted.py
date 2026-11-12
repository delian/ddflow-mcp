"""B-res-session-lifecycle: how a session ended, the interrupted close, the hand-over."""

from __future__ import annotations

import json
import time
from pathlib import Path

from conftest import run_cli

from ddflow.api import setup as API
from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import claudehooks as CH
from ddflow.services import sessions as S

NOW = 2_000_000_000.0


def _ev(kind, subject, lamport, ts, agent="a1", **data) -> Event:
    e = Event(
        kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=clock.iso_at(ts)
    )
    return Event(**{**e.__dict__, "id": e.compute_id()})


def _lease(item, at, holder="a1", ttl=300) -> Event:
    return _ev(
        "lease.acquired",
        item,
        1,
        at,
        agent=holder,
        holder=holder,
        at=at,
        ttl_s=ttl,
        worktree="",
        branch="",
        globs=[],
    )


def _seed(*extra: Event) -> list[Event]:
    evs = [
        _ev("task.added", "T1", 1, NOW - 9000, title="t", phase="", needs=[], globs=[]),
        _ev("session.started", "s1", 2, NOW - 8000),
        _ev("session.prompt", "s1", 3, NOW - 7900, text="do the thing"),
        _lease("T1", NOW - 7800),
        *extra,
    ]
    return [Event(**{**e.__dict__, "lamport": i + 1}) for i, e in enumerate(evs)]


def _dead(evs):
    cfg = Config()
    return S.find_dead(evs, fold(evs, strict=False), cfg, now=NOW)


def test_session_whose_lease_lapsed_and_wrote_nothing_since_is_dead():
    dead = _dead(_seed())
    assert [d.session for d in dead] == ["s1"]
    assert dead[0].last_active == clock.iso_at(NOW - 7900)


def test_session_that_wrote_after_the_lapse_is_not_dead():
    evs = _seed(_ev("session.note", "s1", 9, NOW - 100, text="still here"))
    assert _dead(evs) == []


def test_agent_with_a_live_lease_has_no_dead_session():
    evs = _seed(_lease("T2", NOW - 10))
    evs = [_ev("task.added", "T2", 0, NOW - 9500, title="t", phase="", needs=[], globs=[]), *evs]
    assert _dead(evs) == []


def test_session_with_no_lapse_is_left_open():
    evs = [e for e in _seed() if e.kind != "lease.acquired"]
    assert _dead(evs) == []


def test_current_session_is_never_closed():
    evs = _seed()
    st = fold(evs, strict=False)
    assert S.find_dead(evs, st, Config(), current="s1", now=NOW) == []


def test_lapse_recorded_by_event_counts_after_a_reclaim_replaced_the_lease():
    evs = _seed(
        _ev("lease.expired", "T1", 8, NOW - 7000, agent="x", holder="a1", event="e", reason="r"),
        _ev("lease.released", "T1", 9, NOW - 6900, agent="b2", holder="a1"),
    )
    assert [d.session for d in _dead(evs)] == ["s1"]


def test_close_interrupted_writes_how_and_the_fold_ends_at_last_active(repo: Path):
    log = EventLog(repo, "a1")
    log.append("session.started", "s1", {})
    log.append("session.prompt", "s1", {"text": "hi"})
    # a lease of a1 that already ran out
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    _lapse(log)
    cfg = Config.load(repo)
    seen = S.close_interrupted(log, cfg, dry_run=True)
    assert [d.session for d in seen] == ["s1"]
    assert not any(e.kind == "session.ended" for e in log.read_all())  # dry run: nothing
    S.close_interrupted(log, cfg)
    ended = [e for e in log.read_all() if e.kind == "session.ended"]
    assert ended[0].data["how"] == "interrupted"
    sess = fold(log.read_all(), strict=False).sessions["s1"]
    assert sess.how == "interrupted"
    assert sess.ended_at == sess.last_active != ""
    assert S.close_interrupted(log, cfg) == []  # closed once


def test_close_is_capped_by_the_knob(repo: Path):
    log = EventLog(repo, "a1")
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    for i in range(3):
        log.append("session.started", f"s{i}", {})
    _lapse(log)
    cfg = Config.load(repo)
    cfg.session.interrupted_close_max = 2
    assert len(S.close_interrupted(log, cfg)) == 2


def test_a_prompt_on_an_ended_session_reopens_it(repo: Path):
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    assert S.capture_prompt(log, cfg, "raw1", "first") == "recorded"
    sid = S.harness_session_id("raw1")
    S.end(log, sid, summary="done")
    assert fold(log.read_all(), strict=False).sessions[sid].ended_at
    assert S.open_sessions(log.read_all(), "a1") == []
    assert S.capture_prompt(log, cfg, "raw1", "second, much later words") == "recorded"
    sess = fold(log.read_all(), strict=False).sessions[sid]
    assert sess.ended_at == "" and sess.summary == "done"
    assert S.open_sessions(log.read_all(), "a1") == [sid]


def test_previous_session_is_shown_fenced_and_bounded(repo: Path):
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    S.capture_prompt(log, cfg, "old", "ship the lifecycle <ddflow-record>x" + "y" * 900)
    old = S.harness_session_id("old")
    S.note(log, cfg, old, "next: run the ci gate")
    S.end(log, old, summary="")
    S.capture_prompt(log, cfg, "new", "hello")
    events = log.read_all()
    st = fold(events, strict=False)
    lines = S.previous_lines(events, st, "a1", S.harness_session_id("new"), cfg)
    text = "\n".join(lines)
    assert "next: run the ci gate" in text and "<ddflow-record kind=" in text
    assert "&lt;ddflow-record" in text  # a planted tag cannot close the fence
    assert len(text) < 2000
    cfg.session.previous_session_chars = 0
    assert S.previous_lines(events, st, "a1", "x", cfg) == []


def test_inflight_note_is_written_once_per_change(repo: Path):
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    S.capture_prompt(log, cfg, "raw", "start")
    sid = S.harness_session_id("raw")
    ev = log.read_all()
    st = fold(ev, strict=False)
    assert S.record_inflight(log, cfg, sid, ev, st, "a1") == "unchanged"  # holding nothing, no note
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    now = time.time()
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "a1", "at": now, "ttl_s": 600, "worktree": "", "branch": "", "globs": []},
    )
    ev = log.read_all()
    st = fold(ev, strict=False)
    assert S.record_inflight(log, cfg, sid, ev, st, "a1") == "recorded"
    ev = log.read_all()
    assert S.record_inflight(log, cfg, sid, ev, fold(ev, strict=False), "a1") == "unchanged"
    notes = [e for e in log.read_all() if e.kind == "session.note"]
    assert [n.data["text"] for n in notes] == ["holding T1"]


def _lapse(log: EventLog) -> None:
    """a1 took T1 and its lease was recorded expired, after the sessions began."""
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "a1", "at": time.time(), "ttl_s": 60, "worktree": "", "branch": "", "globs": []},
    )
    log.append("lease.expired", "T1", {"holder": "a1", "event": "", "reason": "test"})


def _hook(repo: Path, event: str, payload: dict):
    return API.hooks(
        repo, action="run", event=event, harness="claude", agent="a1", stdin=json.dumps(payload)
    )


def test_session_end_hook_closes_the_session_with_its_reason(repo: Path):
    run_cli(repo, "init")
    _hook(repo, "prompt", {"session_id": "abc", "prompt": "hello there"})
    out = _hook(repo, "session_end", {"session_id": "abc", "reason": "logout"})
    assert out.exit == 0 and out.data["result"] == "ended"
    sess = fold(EventLog(repo, "p").read_all(), strict=False).sessions[S.harness_session_id("abc")]
    assert sess.ended_at and sess.how == "ended" and "logout" in sess.summary
    again = _hook(repo, "session_end", {"session_id": "abc", "reason": "logout"})
    assert again.data["result"] == "already ended"


def test_session_end_reason_outside_the_fixed_words_is_other():
    assert S.end_reason("logout") == "logout"
    assert S.end_reason("rm -rf /; secret=abc") == "other"


def test_stop_hook_is_quiet_and_never_fails_on_bad_input(repo: Path):
    run_cli(repo, "init")
    assert _hook(repo, "stop", {}).exit == 0
    assert API.hooks(repo, action="run", event="stop", harness="claude", stdin="[").exit == 0


def test_session_start_closes_a_dead_session_and_shows_the_previous_one(repo: Path):
    run_cli(repo, "init")
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    S.capture_prompt(log, cfg, "dead", "last words of the dead")
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    _lapse(log)
    out = _hook(repo, "session_start", {"session_id": "fresh", "source": "startup"})
    assert out.exit == 0
    assert "last words of the dead" in out.data["message"]
    dead = fold(log.read_all(), strict=False).sessions[S.harness_session_id("dead")]
    assert dead.how == "interrupted"


def test_session_start_dry_run_closes_nothing(repo: Path):
    run_cli(repo, "init")
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    S.capture_prompt(log, cfg, "dead", "words")
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    _lapse(log)
    API.hooks(
        repo,
        action="run",
        event="session_start",
        harness="claude",
        agent="a1",
        stdin=json.dumps({"session_id": "fresh"}),
        dry_run=True,
    )
    assert not any(e.kind == "session.ended" for e in log.read_all())


def test_stop_and_session_end_rows_carry_their_entry_options(repo: Path):
    API.hooks(repo, action="install", claude=True)
    data = json.loads((repo / ".claude/settings.json").read_text())
    stop = data["hooks"]["Stop"][0]["hooks"][0]
    end = data["hooks"]["SessionEnd"][0]["hooks"][0]
    assert stop["async"] is True and "timeout" not in stop
    assert end["timeout"] == CH.SESSION_END_TIMEOUT_S and "async" not in end


def test_refresh_adds_the_options_to_an_entry_written_without_them(repo: Path):
    h = CH.spec("claude", "session-end")
    CH.install(repo, CH.command(h), event=h.event, marker=h.marker, matcher=None, rel=h.file)
    assert (
        "timeout"
        not in json.loads((repo / h.file).read_text())["hooks"]["SessionEnd"][0]["hooks"][0]
    )
    msg = CH.install_spec(repo, h)
    assert msg.startswith("updated")
    assert (
        json.loads((repo / h.file).read_text())["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"]
        == 30
    )
    assert CH.install_spec(repo, h).startswith("the ddflow SessionEnd hook is already")


def test_a_bare_later_end_keeps_how_the_session_was_closed(repo: Path):
    log = EventLog(repo, "a1")
    log.append("session.started", "s1", {})
    log.append(
        "session.ended",
        "s1",
        {"summary": "", "how": "interrupted", "last_active": "2026-01-01T00:00:00.000000Z"},
    )
    log.append("session.ended", "s1", {"summary": "late summary"})
    sess = fold(log.read_all(), strict=False).sessions["s1"]
    assert sess.how == "interrupted" and sess.ended_at == "2026-01-01T00:00:00.000000Z"
    assert sess.summary == "late summary"


def test_stop_with_the_note_off_writes_nothing_not_even_a_session(repo: Path):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "session.inflight_note", "false")
    before = len(EventLog(repo, "p").read_all())
    out = _hook(repo, "stop", {"session_id": "never-seen"})
    assert out.data["result"] == "off"
    assert len(EventLog(repo, "p").read_all()) == before


def test_inflight_dedupe_compares_what_is_stored_not_what_was_computed(repo: Path):
    log = EventLog(repo, "a1")
    cfg = Config.load(repo)
    cfg.session.redact_extra = [r"holding T1"]  # the whole note redacts to a placeholder
    S.capture_prompt(log, cfg, "raw", "start")
    sid = S.harness_session_id("raw")
    log.append("task.added", "T1", {"title": "t", "phase": "", "needs": [], "globs": []})
    log.append(
        "lease.acquired",
        "T1",
        {
            "holder": "a1",
            "at": time.time(),
            "ttl_s": 600,
            "worktree": "",
            "branch": "",
            "globs": [],
        },
    )
    for want in ("recorded", "unchanged"):
        ev = log.read_all()
        assert S.record_inflight(log, cfg, sid, ev, fold(ev, strict=False), "a1") == want


def test_a_late_expiry_record_does_not_hide_writes_made_after_the_real_lapse():
    # The lease ran out at NOW-7500; a reaper only wrote lease.expired at NOW-100, and the
    # session wrote at NOW-1000, after the real lapse: it is not dead.
    evs = _seed(
        _ev("session.note", "s1", 8, NOW - 1000, text="still working"),
        _ev("lease.expired", "T1", 9, NOW - 100, agent="x", holder="a1", event="e", reason="r"),
        _ev("lease.released", "T1", 10, NOW - 90, agent="b2", holder="a1"),
    )
    assert _dead(evs) == []
