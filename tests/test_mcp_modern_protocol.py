"""B149: the server negotiates `2026-07-28` -- and honours what that revision REQUIRES.

`2026-07-28` is stateless: no `initialize`; each request names its protocol version and
the client's capabilities in `params._meta`. A server MUST answer `server/discover`, MUST
refuse a version it does not serve with -32022 naming the ones it does, MUST refuse a
request missing a required `_meta` field with -32602, and every result MUST carry a
`resultType`; list/read results carry `ttlMs` + `cacheScope`, and each result SHOULD name
the server in `_meta`. The server is dual-era: `initialize` keeps selecting the legacy
revisions, byte-for-byte as before.
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from ddflow.surfaces import mcp as M
from ddflow.surfaces.mcp import SERVER_INFO, SUPPORTED_PROTOCOLS, Server, serve

MODERN = "2026-07-28"
SERVER_KEY = "io.modelcontextprotocol/serverInfo"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def meta(version=MODERN, caps=True, **extra):
    m = {"io.modelcontextprotocol/protocolVersion": version}
    if caps:
        m["io.modelcontextprotocol/clientCapabilities"] = {}
    m.update(extra)
    return m


def req(method, params=None, mid=1, **meta_kw):
    p = dict(params or {})
    p["_meta"] = meta(**meta_kw)
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": p}


def call(repo, msg):
    return Server(repo, agent="modern").handle(msg)


def test_discover_names_the_versions_capabilities_and_identity(repo):
    res = call(repo, req("server/discover"))["result"]
    assert res["supportedVersions"][0] == MODERN
    assert set(SUPPORTED_PROTOCOLS) <= set(res["supportedVersions"])
    assert set(res["capabilities"]) == {"tools", "resources", "prompts"}
    assert res["instructions"].strip()
    assert res["resultType"] == "complete"
    assert res["_meta"][SERVER_KEY] == SERVER_INFO
    assert res["cacheScope"] == "private" and isinstance(res["ttlMs"], int)


def test_discover_without_the_version_is_malformed_not_unknown(repo):
    """-32601 would tell a dual-era client's probe that this is a LEGACY server."""
    r = call(repo, {"jsonrpc": "2.0", "id": 7, "method": "server/discover", "params": {}})
    assert r["error"]["code"] == -32602 and r["id"] == 7


@pytest.mark.parametrize("version", ["1900-01-01", "2025-06-18", "2099-12-31"])
def test_a_version_not_served_per_request_is_refused_with_32022(repo, version):
    r = call(repo, req("tools/list", version=version))
    err = r["error"]
    assert err["code"] == -32022
    assert err["data"]["requested"] == version
    assert MODERN in err["data"]["supported"]


def test_a_non_string_version_is_invalid_params(repo):
    assert call(repo, req("tools/list", version=20260728))["error"]["code"] == -32602


def test_missing_client_capabilities_is_invalid_params(repo):
    r = call(repo, req("tools/list", caps=False))
    assert r["error"]["code"] == -32602 and "clientCapabilities" in r["error"]["message"]


def test_a_malformed_modern_notification_gets_no_reply(repo):
    msg = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"_meta": meta("x")}}
    assert call(repo, msg) is None


def test_a_modern_notification_is_never_answered_even_by_discover(repo):
    """Review finding: `server/discover` sent without an id got a result with id null."""
    for method in ("server/discover", "tools/list", "notifications/cancelled"):
        msg = req(method)
        del msg["id"]
        assert call(repo, msg) is None, method


def test_modern_tools_list_is_the_same_list_with_cache_hints(repo):
    legacy = call(repo, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]
    modern = call(repo, req("tools/list"))["result"]
    assert modern["tools"] == legacy["tools"]
    assert modern["resultType"] == "complete"
    assert modern["ttlMs"] == 3_600_000 and modern["cacheScope"] == "private"
    assert modern["_meta"][SERVER_KEY] == SERVER_INFO


@pytest.mark.parametrize(
    "method,params",
    [
        ("resources/list", {}),
        ("resources/read", {"uri": "ddflow://decisions"}),
        ("prompts/list", {}),
    ],
)
def test_every_cacheable_result_carries_ttl_and_scope(repo, method, params):
    res = call(repo, req(method, params))["result"]
    assert res["resultType"] == "complete"
    assert isinstance(res["ttlMs"], int) and res["cacheScope"] == "private"


def test_a_modern_tool_call_needs_no_handshake_and_keeps_its_exit(repo):
    """Stateless: a fresh connection, no `initialize`, and the call is served."""
    r = call(repo, req("tools/call", {"name": "ddflow_status", "arguments": {}}))
    res = r["result"]
    assert res["resultType"] == "complete"
    assert "exit" in res["_meta"] and res["_meta"][SERVER_KEY] == SERVER_INFO
    assert "ttlMs" not in res  # tools/call is not a cacheable result


def test_errors_stay_errors_in_the_modern_era(repo):
    r = call(repo, req("resources/read", {"uri": "ddflow://nope"}))
    assert r["error"]["code"] == -32602  # never the retired -32002
    assert "result" not in r


def test_the_legacy_era_is_unchanged(repo):
    """No `_meta` version: exactly the old shapes -- no resultType, no cache hints."""
    srv = Server(repo, agent="legacy")
    init = srv.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": MODERN}}
    )["result"]
    assert init["protocolVersion"] == SUPPORTED_PROTOCOLS[0]  # `initialize` is legacy-only
    assert "resultType" not in init
    listed = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]
    assert set(listed) == {"tools"}


def test_an_offloaded_call_with_a_bad_version_is_refused_without_a_worker(repo, monkeypatch):
    def no_worker(*a, **k):
        raise AssertionError("a worker was started for a refused request")

    monkeypatch.setattr(M, "_offload", no_worker)
    msg = req("tools/call", {"name": "ddflow_wait", "arguments": {}}, version="1900-01-01")
    out = io.StringIO()
    serve(repo, stdin=io.StringIO(json.dumps(msg) + "\n"), stdout=out, offload=True)
    frames = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    assert [f["error"]["code"] for f in frames] == [-32022]


def test_offload_own_replies_are_modern(repo, monkeypatch):
    """`_offload`'s own answers (here: busy) are shaped like every other modern result."""

    def busy(srv, msg, send, n):
        send(M._ok(msg["id"], M._text("busy", meta={"exit": 2})))

    monkeypatch.setattr(M, "_offload", busy)
    msg = req("tools/call", {"name": "ddflow_wait", "arguments": {}})
    out = io.StringIO()
    serve(repo, stdin=io.StringIO(json.dumps(msg) + "\n"), stdout=out, offload=True)
    sent = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    assert sent[0]["result"]["resultType"] == "complete"
    assert sent[0]["result"]["_meta"] == {"exit": 2, SERVER_KEY: SERVER_INFO}


# -- D-mcp-identity-per-call: on the stateless revision, identity is per request ----------


def _tool(srv, name, args, *, modern=True, mid=1, **meta_kw):
    msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
           "params": {"name": name, "arguments": args}}  # fmt: skip
    if modern:
        msg = req("tools/call", {"name": name, "arguments": args}, mid=mid, **meta_kw)
    return srv.handle(msg)["result"]


def _project(repo):
    from conftest import run_cli

    assert run_cli(repo, "init")[0] == 0
    for t in ("T1", "T2", "T3"):
        assert run_cli(repo, "task", "add", t, "--globs", f"{t}.py")[0] == 0


def _authors(repo):
    from ddflow.infra.log import EventLog

    return {e.subject: e.agent for e in EventLog(repo).read_all() if e.kind == "task.updated"}


def test_a_modern_identify_persists_nothing_and_says_why(repo):
    _project(repo)
    srv = Server(repo, agent="")
    before = srv.agent
    res = _tool(srv, "ddflow_identify", {"agent": "sub-x"})
    text = res["content"][0]["text"]
    assert not res.get("isError"), text
    assert "per request" in text and "as_agent" in text and M.META_AGENT in text
    assert srv.agent == before, "a modern identify changed the connection's identity"
    _tool(srv, "ddflow_update", {"id": "T1", "title": "after identify"}, mid=2)
    assert _authors(repo)["T1"] != "sub-x"


def test_meta_agent_and_as_agent_name_the_caller_per_request(repo):
    _project(repo)
    srv = Server(repo, agent="")
    _tool(srv, "ddflow_update", {"id": "T1", "title": "a"}, **{M.META_AGENT: "via-meta"})
    _tool(srv, "ddflow_update", {"id": "T2", "title": "b", "as_agent": "via-arg"}, mid=2)
    # the argument is the more specific of the two
    _tool(
        srv, "ddflow_update", {"id": "T3", "title": "c", "as_agent": "arg-wins"}, mid=3,
        **{M.META_AGENT: "meta-loses"},
    )  # fmt: skip
    assert _authors(repo) == {"T1": "via-meta", "T2": "via-arg", "T3": "arg-wins"}
    assert srv.agent == "", "a per-request name leaked into the connection"


def test_a_bad_meta_agent_is_refused_as_a_tool_error(repo):
    _project(repo)
    srv = Server(repo, agent="")
    res = _tool(srv, "ddflow_update", {"id": "T1", "title": "a"}, **{M.META_AGENT: "a b/c"})
    assert res.get("isError") and "not a usable agent name" in res["content"][0]["text"]


def test_a_modern_request_without_identity_uses_the_derived_default(repo):
    from ddflow.surfaces.mcp import _default_agent

    _project(repo)
    srv = Server(repo, agent="")
    _tool(srv, "ddflow_update", {"id": "T1", "title": "a"})
    assert _authors(repo)["T1"] == _default_agent(repo)[0]


def test_an_initialize_era_connection_keeps_identify(repo):
    _project(repo)
    srv = Server(repo, agent="")
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": SUPPORTED_PROTOCOLS[0]}})  # fmt: skip
    _tool(srv, "ddflow_identify", {"agent": "legacy-a"}, modern=False, mid=2)
    assert srv.agent == "legacy-a"
    _tool(srv, "ddflow_update", {"id": "T1", "title": "a"}, modern=False, mid=3)
    # `_meta` agent is a modern-era field: a legacy request's `_meta` is not read for it
    srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "ddflow_update", "arguments": {"id": "T2", "title": "b"},
        "_meta": {M.META_AGENT: "ignored"}}})  # fmt: skip
    assert _authors(repo) == {"T1": "legacy-a", "T2": "legacy-a"}


# -- Bac0bb04c9f: 2025-11-25, the last initialize-era revision ----------------------------

LATEST_LEGACY = "2025-11-25"


def _init(repo, version):
    return Server(repo, agent="legacy").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}}
    )["result"]


def test_initialize_with_2025_11_25_is_answered_with_2025_11_25(repo):
    assert _init(repo, LATEST_LEGACY)["protocolVersion"] == LATEST_LEGACY


def test_an_unknown_initialize_version_falls_back_to_the_newest_legacy_one(repo):
    assert _init(repo, "2099-01-01")["protocolVersion"] == LATEST_LEGACY
    # The older revisions are still answered with themselves.
    for older in ("2025-06-18", "2025-03-26", "2024-11-05"):
        assert _init(repo, older)["protocolVersion"] == older


def test_discover_lists_2025_11_25(repo):
    res = call(repo, req("server/discover"))["result"]
    assert LATEST_LEGACY in res["supportedVersions"]


def test_tool_names_follow_the_2025_11_25_naming_guidance(repo):
    """SEP-986: 1-128 characters of A-Z a-z 0-9 _ - . -- unique, case-sensitive."""
    import re

    names = [t["name"] for t in Server(repo, agent="x").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )["result"]["tools"]]  # fmt: skip
    assert names and len(set(names)) == len(names)
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", n) for n in names), names


def test_input_validation_errors_are_tool_execution_errors(repo):
    """SEP-1303: a bad argument is a tool result with isError, which the model can read
    and correct -- never a JSON-RPC protocol error."""
    srv = Server(repo, agent="x")
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": LATEST_LEGACY},
        }
    )
    r = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            # `id` given: the UNKNOWN argument is what is refused, not a missing one.
            "params": {"name": "ddflow_show", "arguments": {"id": "T1", "no_such_argument": 1}},
        }
    )
    assert "error" not in r and r["result"]["isError"] is True
    assert (
        "unknown argument(s) for ddflow_show: no_such_argument" in r["result"]["content"][0]["text"]
    )


def test_a_valid_as_agent_wins_over_a_malformed_meta_name(repo):
    """Rubber-duck and critic: `_meta` was validated first, so a bad `_meta` name refused
    a call whose `as_agent` -- documented to win -- was fine."""
    _project(repo)
    srv = Server(repo, agent="")
    res = _tool(
        srv, "ddflow_update", {"id": "T1", "title": "a", "as_agent": "alice"},
        **{M.META_AGENT: "not a name!"},
    )  # fmt: skip
    assert not res.get("isError"), res["content"][0]["text"]
    assert _authors(repo)["T1"] == "alice"
