"""Hold-and-wait is gone: a refused widening or claim returns at once and leaves a
`lease.waiting` record; the records are the wait-for graph (DX-no-hold-and-wait).

2026-10-15: three agents held their old globs and polled for each other's, 45+ minutes.
Now the refusal registers who waits for whom (a logged, folded, replayable event), says
which of the waiter's OWN globs others are waiting for once the wait outlasts
`[lease].wait_max_s`, and the record ends with the waiter's lease."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import waiting as WG

A, B = "agent-a", "agent-b"


def _two_holders(repo) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    for item, g in (("T1", "src/a.py"), ("T2", "src/b.py")):
        assert run_cli(repo, "task", "add", item, "--title", item, "--globs", g, agent=A)[0] == 0
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 0


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _cfg(repo):
    return Config.load(repo)


def _cross_widen(repo):
    t = time.monotonic()
    ra = run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    rb = run_cli(repo, "update", "T2", "--globs", "src/b.py,src/a.py", agent=B)
    return ra, rb, time.monotonic() - t


def test_two_crossed_widenings_register_two_waits_and_the_graph_shows_the_cycle(repo):
    _two_holders(repo)
    (ca, *_), (cb, *_), _ = _cross_widen(repo)
    assert ca == 3 and cb == 3  # refused, exit 3
    st = _state(repo)
    assert set(st.waiting) == {"T1", "T2"}
    assert st.waiting["T1"]["holder"] == A and st.waiting["T2"]["holder"] == B
    graph = WG.wait_for_graph(st, _cfg(repo), time.time())
    assert graph == {"T1": ["T2"], "T2": ["T1"]}  # the cycle, as data


def test_a_refusal_returns_at_once_and_keeps_the_old_globs(repo):
    _two_holders(repo)
    (code, _out, err), _rb, took = _cross_widen(repo)
    assert code == 3 and "waiting" in err
    assert took < 20  # no private polling
    assert _state(repo).items["T1"].lease.globs == ["src/a.py"]  # nothing taken, nothing lost


def test_asking_again_does_not_grow_the_log_and_keeps_since(repo):
    _two_holders(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    first = _state(repo).waiting["T1"]
    n = len(EventLog(repo, "r").read_all())
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    assert len(EventLog(repo, "r").read_all()) == n
    assert _state(repo).waiting["T1"]["since"] == first["since"]


def test_the_wait_ends_with_the_waiters_lease(repo):
    _two_holders(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    st, cfg = _state(repo), _cfg(repo)
    now = time.time()
    assert "T1" in WG.standing(st, cfg, now)
    later = now + cfg.lease.ttl_s + cfg.lease.grace_s + 60  # T1's lease lapsed
    assert WG.standing(st, cfg, later) == {}
    run_cli(repo, "release", "T1", agent=A)
    assert "T1" not in WG.standing(_state(repo), cfg, time.time())


def test_the_wait_ends_when_the_holder_lets_go(repo):
    _two_holders(repo)
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    run_cli(repo, "release", "T2", agent=B)
    assert WG.wait_for_graph(_state(repo), _cfg(repo), time.time()) == {}


def test_a_refused_claim_registers_a_wait_and_expires_by_ttl(repo):
    _two_holders(repo)
    assert (
        run_cli(repo, "task", "add", "T3", "--title", "t", "--globs", "src/a.py", agent=A)[0] == 0
    )
    code, _out, err = run_cli(repo, "claim", "T3", "--no-worktree", agent="agent-c")
    assert code == 3 and "waiting" in err
    st, cfg = _state(repo), _cfg(repo)
    assert WG.wait_for_graph(st, cfg, time.time()) == {"T3": ["T1"]}
    assert WG.standing(st, cfg, time.time() + cfg.lease.ttl_s + 5) == {}


def test_a_long_wait_names_the_own_globs_others_wait_for(repo):
    _two_holders(repo)
    _cross_widen(repo)
    st, cfg = _state(repo), _cfg(repo)
    now = time.time() + cfg.lease.wait_max_s + 5
    adv = WG.release_advice(st, cfg, "T1", now)
    assert adv and adv["release"] == ["src/a.py"] and adv["waiters"][0]["holder"] == B
    assert "update T1 --globs" in WG.advice_text("T1", adv, ["src/a.py"])
    assert WG.release_advice(st, cfg, "T1", time.time()) is None  # not yet over the limit
    assert WG.waiting_for(st, cfg, "T1", time.time())[0]["item"] == "T2"


def test_a_queued_request_is_served_first_come_first_served(repo):
    _two_holders(repo)
    assert run_cli(repo, "config", "lease.wait_queue", "true", agent=A)[0] == 0
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    assert (
        run_cli(repo, "task", "add", "T3", "--title", "t", "--globs", "src/b.py", agent=A)[0] == 0
    )
    code, _out, err = run_cli(repo, "claim", "T3", "--no-worktree", agent="agent-c")
    assert code == 3 and "queued behind" in err
    run_cli(repo, "release", "T2", agent=B)
    code, _out, err = run_cli(repo, "claim", "T3", "--no-worktree", agent="agent-c")
    assert code == 3 and "queued behind" in err  # agent-a asked first


def test_the_waits_are_shown_in_status_brief_and_lanes(repo):
    _two_holders(repo)
    _cross_widen(repo)
    code, out, _ = run_cli(repo, "--json", "status", agent=A)
    assert code == 0 and "T1" in out and "waiting" in out
    code, out, _ = run_cli(repo, "status", agent=A)
    assert "Waiting for files:" in out and "agent-a on T1 waits" in out
    code, out, _ = run_cli(repo, "brief", agent=A)
    assert "## Waiting for files" in out
    code, out, _ = run_cli(repo, "lanes", agent=A)
    assert "T1 waits for T2 (agent-b)" in out, out


def test_wait_max_s_zero_never_tells_the_waiter_to_release(repo):
    _two_holders(repo)
    _cross_widen(repo)
    st, cfg = _state(repo), _cfg(repo)
    cfg.lease.wait_max_s = 0
    assert WG.release_advice(st, cfg, "T1", time.time() + 100000) is None


def test_wait_queue_knob_queues_every_refusal(repo):
    _two_holders(repo)
    assert run_cli(repo, "config", "lease.wait_queue", "true", agent=A)[0] == 0
    run_cli(repo, "update", "T1", "--globs", "src/a.py,src/b.py", agent=A)
    assert _state(repo).waiting["T1"]["queue"] is True


def test_claim_queue_flag_queues_a_refused_claim(repo):
    _two_holders(repo)
    assert (
        run_cli(repo, "task", "add", "T3", "--title", "t", "--globs", "src/a.py", agent=A)[0] == 0
    )
    assert run_cli(repo, "claim", "T3", "--no-worktree", "--queue", agent="agent-c")[0] == 3
    assert _state(repo).waiting["T3"]["queue"] is True
