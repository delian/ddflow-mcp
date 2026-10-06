"""D-mcp-identity-per-call: a stateless request's `_meta` agent is a per-call name, like
`as_agent`, for where a claim stands too.

The server stands where its harness started it (`called_from`). A request that names an
agent OTHER than the connection's own -- by `as_agent` or, on the `2026-07-28` revision,
by `ddflow/agent` in its `_meta` -- cannot be told apart from a subagent riding the
connection, and adopting the server's tree for it is the bug B7c7a0d9222 fixed (a
subagent bound to its parent's tree and branch). So it is answered as from the primary:
the item gets a tree of its own. Naming the connection's own identity, or nobody, still
adopts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_asagent_claim import _item, _setup

from ddflow.surfaces import mcp as M
from ddflow.surfaces.mcp import _default_agent


def _modern_claim(srv, iid: str, **meta) -> dict:
    params = {
        "name": "ddflow_claim",
        "arguments": {"id": iid},
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": M.MODERN_PROTOCOLS[0],
            "io.modelcontextprotocol/clientCapabilities": {},
            **meta,
        },
    }
    return srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})[
        "result"
    ]


def test_a_meta_named_caller_does_not_adopt_the_servers_tree(repo):
    srv, _tree = _setup(repo)
    out = _modern_claim(srv, "T1", **{M.META_AGENT: "sub-1"})
    assert out["_meta"]["exit"] == 0, out
    t1 = _item(repo, "T1")
    assert t1.adopted is False and "parent-tree" not in (t1.worktree or ""), t1.worktree
    assert t1.lease.holder == "sub-1"


def test_a_meta_name_that_is_the_connections_own_still_adopts(repo):
    srv, _tree = _setup(repo)
    who, _src = _default_agent(repo)
    out = _modern_claim(srv, "T1", **{M.META_AGENT: who})
    assert out["_meta"]["exit"] == 0, out
    assert _item(repo, "T1").adopted is True


def test_a_modern_claim_naming_nobody_still_adopts(repo):
    srv, _tree = _setup(repo)
    out = _modern_claim(srv, "T1")
    assert out["_meta"]["exit"] == 0, out
    assert _item(repo, "T1").adopted is True
