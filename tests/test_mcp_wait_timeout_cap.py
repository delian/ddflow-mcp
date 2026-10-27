"""MCP `ddflow_wait` must never let its answer collide with the client's idle cutoff
(Bfc43dae5b9): a client aborts a tool call that sends nothing for 1800 s, and a wait
whose client sent no `_meta.progressToken` has no channel at all -- not even the
B7b3b4cf483 heartbeat, which needs the token to reset the idle timer. Today the cap is
1800 too, so such a call can still be aborted exactly where the client cuts it.
"""

from __future__ import annotations

from ddflow.surfaces.tools._common import (
    CLIENT_IDLE_ABORT_S,
    MCP_WAIT_DEFAULT_S,
    MCP_WAIT_MAX_S,
    _wait_timeout,
)


def test_the_mcp_wait_cap_is_strictly_below_the_client_idle_cutoff():
    # The cutoff a silent client aborts at; nothing above this cap may be chosen per
    # call, because no progress is possible for a token-less wait, so the cap IS the
    # longest the answer may take.
    assert CLIENT_IDLE_ABORT_S == 1800
    assert MCP_WAIT_MAX_S < CLIENT_IDLE_ABORT_S


def test_a_wait_of_the_full_idle_window_is_shortened_below_it():
    # The low side of the clamp is untouched: the default and any supplied value sit
    # at or below the cap, which the test above pins below the idle cutoff.
    assert MCP_WAIT_DEFAULT_S <= MCP_WAIT_MAX_S
    assert _wait_timeout({}) == MCP_WAIT_DEFAULT_S
    given = _wait_timeout({"timeout": 1800})
    huge = _wait_timeout({"timeout": 10**9})
    assert 0 < given < 1800, "the reported bug: a 1800 s wait, clamped at 1800 s"
    assert 0 < huge < 1800
