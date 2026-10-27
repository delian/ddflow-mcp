"""MCP `ddflow_wait` must never let its answer collide with the client's idle cutoff
(Bfc43dae5b9): a client aborts a tool call that sends nothing for 1800 s, and a wait
whose call can send no `notifications/progress` has no channel at all -- not even the
B7b3b4cf483 heartbeat, which needs a `_meta.progressToken` -- so its cap must sit below
the cutoff. A call that CAN send progress keeps the full CLI cap: every beat resets the
client's idle timer.
"""

from __future__ import annotations

from ddflow.surfaces.tools._common import (
    CLIENT_IDLE_ABORT_S,
    MCP_WAIT_DEFAULT_S,
    MCP_WAIT_MAX_S,
    MCP_WAIT_SILENT_MAX_S,
    _wait_timeout,
)


def test_the_silent_wait_cap_is_strictly_below_the_client_idle_cutoff():
    # The cutoff a silent client aborts at; nothing above this cap may be chosen per
    # call, because no progress is possible, so the cap IS the longest the answer may
    # take -- with headroom for however long the call sat silent before its wait began.
    assert CLIENT_IDLE_ABORT_S == 1800
    assert MCP_WAIT_SILENT_MAX_S < CLIENT_IDLE_ABORT_S


def test_a_silent_wait_of_the_full_idle_window_is_shortened_below_it(repo):
    # The reported bug: a token-less wait asked for the full window must never run to
    # it, and the default sits under the silent cap.
    assert MCP_WAIT_DEFAULT_S <= MCP_WAIT_SILENT_MAX_S
    assert _wait_timeout({}, repo) == MCP_WAIT_DEFAULT_S
    given = _wait_timeout({"timeout": 1800}, repo)
    huge = _wait_timeout({"timeout": 10**9}, repo)
    assert 0 < given < CLIENT_IDLE_ABORT_S, f"a 1800 s wait held to {given} s"
    assert huge == MCP_WAIT_SILENT_MAX_S < CLIENT_IDLE_ABORT_S


def test_a_wait_that_can_send_progress_keeps_the_full_cap(repo):
    # With a progress token the heartbeat resets the client's idle timer, so holding
    # the answer for the full CLI cap is safe where a silent wait is not.
    tokened = {"timeout": 1800, "_meta": {"progressToken": "t"}}
    assert _wait_timeout(tokened, repo) == MCP_WAIT_MAX_S
    assert MCP_WAIT_MAX_S == CLIENT_IDLE_ABORT_S  # the CLI cap, full 1800 s


def test_a_tokened_wait_is_silent_capped_when_progress_is_off(repo):
    # A token alone is not progress: `[mcp].progress_s = 0` means no beats, so the call
    # reverts to the silent cap.
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[mcp]\nprogress_s = 0\n")
    tokened = {"timeout": 1800, "_meta": {"progressToken": "t"}}
    assert _wait_timeout(tokened, repo) == MCP_WAIT_SILENT_MAX_S


def test_a_nonconformant_progress_token_is_no_token(repo):
    # A list/dict/bool token cannot carry progress (the sender refuses it), so the cap
    # is the silent one.
    for bad in (["a"], {"a": 1}, True):
        assert (
            _wait_timeout({"timeout": 1800, "_meta": {"progressToken": bad}}, repo)
            == MCP_WAIT_SILENT_MAX_S
        )
