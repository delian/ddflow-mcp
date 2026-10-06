"""B-uni-mcp-protocol: every reply the server gives is valid against the OFFICIAL MCP schema
of the revision it was served under.

The schemas under `tests/fixtures/mcp-schema/<revision>/schema.json` are the upstream files,
byte for byte (`manifest.json` names the commit and each file's sha256, and a test holds
them to it). The validator below is the small JSON Schema subset those files use -- no
`jsonschema` dependency -- and refuses any keyword it does not implement, so a newer schema
cannot pass by being half-read.

What is replayed, per revision:
- the handshake (`initialize` for the legacy revisions, `server/discover` for 2026-07-28);
- `ping`, `tools/list`, `resources/list`, every `resources/read`, `prompts/list` and every
  `prompts/get`;
- EVERY tool's result envelope (each tool called with an argument it does not have, which
  every tool answers before running), and a real call of the offline read-only tools and
  of a write/refusal path, so the bodies that ship are checked too;
- the protocol errors (unknown method, unknown resource, unknown prompt, a version not
  served, a modern request missing its capabilities);
- a multi round-trip `InputRequiredResult` and its retry.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ddflow.surfaces import mcp as M
from ddflow.surfaces import mcp_protocol as P

FIXTURES = Path(__file__).parent / "fixtures" / "mcp-schema"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
LEGACY = list(P.SUPPORTED_PROTOCOLS)
MODERN = list(P.MODERN_PROTOCOLS)

# -- a JSON Schema validator for the subset the MCP schemas use --------------------------

#: Keywords that only annotate; everything else must be implemented below or the schema is
#: refused (`_KNOWN`), so a revision using a new keyword fails loudly instead of passing.
_ANNOTATIONS = {"description", "$schema", "format", "title", "default", "examples", "deprecated"}
_KNOWN = _ANNOTATIONS | {
    "$ref",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "anyOf",
    "allOf",
    "const",
    "enum",
    "minimum",
    "maximum",
    "maxItems",
    "$defs",
    "definitions",
}


def _is(value, typ):
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "null": value is None,
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }[typ]


def _deref(schema, root):
    ref = schema["$ref"]
    assert ref.startswith("#/"), ref
    target = root
    for part in ref[2:].split("/"):
        target = target[part]
    return target


def _scalar(schema, value, path):
    out = []
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: {value!r} != const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    if "minimum" in schema and _is(value, "number") and value < schema["minimum"]:
        out.append(f"{path}: {value} < {schema['minimum']}")
    if "maximum" in schema and _is(value, "number") and value > schema["maximum"]:
        out.append(f"{path}: {value} > {schema['maximum']}")
    return out


def _object(schema, value, root, path):
    out = [f"{path}: missing required {k!r}" for k in schema.get("required", []) if k not in value]
    props = schema.get("properties", {})
    for key, sub in value.items():
        if key in props:
            out += errors(props[key], sub, root, f"{path}.{key}")
        elif "additionalProperties" in schema:
            out += errors(schema["additionalProperties"], sub, root, f"{path}.{key}")
    return out


def _array(schema, value, root, path):
    out = []
    if "items" in schema:
        for i, sub in enumerate(value):
            out += errors(schema["items"], sub, root, f"{path}[{i}]")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        out.append(f"{path}: {len(value)} items > maxItems {schema['maxItems']}")
    return out


def errors(schema, value, root, path="$"):
    """Every way `value` violates `schema` (a list of strings; empty when it conforms)."""
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here"]
    unknown = set(schema) - _KNOWN
    assert not unknown, f"validator does not implement {sorted(unknown)} (at {path})"
    out = errors(_deref(schema, root), value, root, path) if "$ref" in schema else []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in types):
            return [*out, f"{path}: {type(value).__name__} is not {types}"]
    out += _scalar(schema, value, path)
    if isinstance(value, dict):
        out += _object(schema, value, root, path)
    if isinstance(value, list):
        out += _array(schema, value, root, path)
    for sub in schema.get("allOf", []):
        out += errors(sub, value, root, path)
    if "anyOf" in schema:
        branches = [errors(sub, value, root, path) for sub in schema["anyOf"]]
        if all(branches):
            best = min(branches, key=len)
            out.append(f"{path}: matches no anyOf branch; closest: {best[:3]}")
    return out


_SCHEMAS: dict[str, dict] = {}


def schema(revision: str) -> dict:
    if revision not in _SCHEMAS:
        _SCHEMAS[revision] = json.loads((FIXTURES / revision / "schema.json").read_text())
    return _SCHEMAS[revision]


def defs(revision: str) -> dict:
    s = schema(revision)
    return s.get("$defs") or s["definitions"]


def ref(revision: str, name: str) -> dict:
    key = "$defs" if "$defs" in schema(revision) else "definitions"
    assert name in defs(revision), f"{revision} has no {name}"
    return {"$ref": f"#/{key}/{name}"}


def conforms(revision: str, name: str, value) -> None:
    found = errors(ref(revision, name), value, schema(revision))
    assert not found, f"{revision} {name}:\n  " + "\n  ".join(found[:12])


# Which definition each successful reply's RESULT must satisfy, and which RESPONSE
# envelope (2026-07-28 types each method's response; earlier revisions have one).
_RESULT = {
    "initialize": "InitializeResult",
    "server/discover": "DiscoverResult",
    "ping": "EmptyResult",
    "tools/list": "ListToolsResult",
    "tools/call": "CallToolResult",
    "resources/list": "ListResourcesResult",
    "resources/read": "ReadResourceResult",
    "prompts/list": "ListPromptsResult",
    "prompts/get": "GetPromptResult",
}
#: 2026-07-28's methods that MAY answer InputRequiredResult (MRTR) -- their response
#: definition is the union, so the reply is checked against the response, too.
_RESPONSE_MODERN = {
    "server/discover": "DiscoverResultResponse",
    "tools/list": "ListToolsResultResponse",
    "tools/call": "CallToolResultResponse",
    "resources/list": "ListResourcesResultResponse",
    "resources/read": "ReadResourceResultResponse",
    "prompts/list": "ListPromptsResultResponse",
    "prompts/get": "GetPromptResultResponse",
}


def check_reply(revision: str, method: str, reply: dict) -> None:
    """A reply as `revision` defines it: a JSON-RPC response, its result the method's."""
    assert reply is not None, f"{method}: no reply"
    if "error" in reply:
        name = (
            "JSONRPCErrorResponse" if "JSONRPCErrorResponse" in defs(revision) else "JSONRPCError"
        )
        conforms(revision, name, reply)
        return
    name = (
        "JSONRPCResultResponse" if "JSONRPCResultResponse" in defs(revision) else "JSONRPCResponse"
    )
    conforms(revision, name, reply)
    if revision in MODERN and method in _RESPONSE_MODERN:
        conforms(revision, _RESPONSE_MODERN[method], reply)
    else:
        conforms(revision, _RESULT[method], reply["result"])


# -- driving the server under each revision ----------------------------------------------


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("conformance")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for k, v in (
        ("user.email", "t@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "init"], check=True
    )
    return root


_IDS = iter(range(1, 10**9))


def _msg(method, params=None, revision=None):
    p = dict(params or {})
    if revision in MODERN:
        p["_meta"] = {
            P.META_VERSION: revision,
            P.META_CLIENT_CAPS: {},
        }
    return {"jsonrpc": "2.0", "id": next(_IDS), "method": method, "params": p}


class Client:
    """One connection under one revision: legacy ones open with `initialize`."""

    def __init__(self, repo, revision):
        self.revision = revision
        self.srv = M.Server(repo, agent="conformance")
        if revision in MODERN:
            self.ask("server/discover")
        else:
            reply = self.ask("initialize", {
                "protocolVersion": revision,
                "capabilities": {},
                "clientInfo": {"name": "conformance", "version": "0"},
            })  # fmt: skip
            assert reply["result"]["protocolVersion"] == revision

    def ask(self, method, params=None):
        reply = self.srv.handle(_msg(method, params, self.revision))
        check_reply(self.revision, method, reply)
        return reply


REVISIONS = [*LEGACY, *MODERN]

#: Tools called for real: offline, read-only, quick, with no required argument.
REAL_CALLS = [
    "ddflow_board",
    "ddflow_brief",
    "ddflow_cadence",
    "ddflow_decision_list",
    "ddflow_doctor",
    "ddflow_flow_show",
    "ddflow_help",
    "ddflow_history",
    "ddflow_identify",
    "ddflow_job_list",
    "ddflow_loops",
    "ddflow_memory_list",
    "ddflow_next",
    "ddflow_progress",
    "ddflow_prompts",
    "ddflow_render",
    "ddflow_replay",
    "ddflow_reviewers_list",
    "ddflow_rule_list",
    "ddflow_status",
    "ddflow_version_show",
    "ddflow_workflow",
]


# -- the fixtures themselves --------------------------------------------------------------


def test_every_served_revision_has_its_official_schema():
    assert sorted(MANIFEST["files"]) == sorted(REVISIONS)


@pytest.mark.parametrize("revision", REVISIONS)
def test_the_vendored_schema_is_the_upstream_file_byte_for_byte(revision):
    """A hand edit to a fixture would make the test agree with the server instead of the
    spec. The digest is the upstream file's at the pinned commit (`manifest.json`)."""
    data = (FIXTURES / revision / "schema.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST["files"][revision]["sha256"]


def test_the_vendored_licence_is_the_upstream_file():
    data = (FIXTURES / "LICENSE").read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST["license_sha256"]


def test_the_validator_can_fail():
    """A validator that accepts everything would make every test below vacuous."""
    rev = MODERN[0]
    with pytest.raises(AssertionError):
        conforms(rev, "CallToolResult", {"content": "not a list", "resultType": "complete"})
    with pytest.raises(AssertionError):
        conforms(rev, "ListToolsResult", {"tools": [], "resultType": "complete"})  # no ttlMs
    with pytest.raises(AssertionError):  # a legacy result is not a modern one
        conforms(rev, "CallToolResult", {"content": [{"type": "text", "text": "x"}]})
    with pytest.raises(AssertionError):
        conforms(LEGACY[0], "Tool", {"name": "x", "inputSchema": {"type": "array"}})


# -- the replies ---------------------------------------------------------------------------


@pytest.mark.parametrize("revision", REVISIONS)
def test_lists_and_reads_conform(repo, revision):
    c = Client(repo, revision)
    c.ask("ping")
    tools = c.ask("tools/list")["result"]["tools"]
    assert tools
    for res in c.ask("resources/list")["result"]["resources"]:
        c.ask("resources/read", {"uri": res["uri"]})
    for prompt in c.ask("prompts/list")["result"]["prompts"]:
        c.ask("prompts/get", {"name": prompt["name"]})


@pytest.mark.parametrize("revision", REVISIONS)
def test_every_tools_result_envelope_conforms(repo, revision):
    c = Client(repo, revision)
    for name in sorted(M.TOOLS):
        res = c.ask("tools/call", {"name": name, "arguments": {"__conformance__": 1}})["result"]
        assert res["isError"] is True, name
    c.ask("tools/call", {"name": "ddflow_no_such_tool", "arguments": {}})


@pytest.mark.parametrize("revision", REVISIONS)
def test_real_tool_results_conform(repo, revision):
    c = Client(repo, revision)
    for name in REAL_CALLS:
        c.ask("tools/call", {"name": name, "arguments": {}})
    # a write, a read of what it wrote, a refusal and a missing argument
    tid = f"T-conf-{revision}"
    c.ask(
        "tools/call", {"name": "ddflow_task_add", "arguments": {"id": tid, "title": "conformance"}}
    )
    c.ask("tools/call", {"name": "ddflow_show", "arguments": {"id": tid}})
    c.ask("tools/call", {"name": "ddflow_complete", "arguments": {"id": tid}})
    c.ask("tools/call", {"name": "ddflow_show", "arguments": {}})


@pytest.mark.parametrize("revision", REVISIONS)
def test_protocol_errors_conform(repo, revision):
    c = Client(repo, revision)
    assert c.ask("no/such/method")["error"]["code"] == -32601
    assert c.ask("resources/read", {"uri": "ddflow://nope"})["error"]["code"] == -32602
    assert c.ask("prompts/get", {"name": "nope"})["error"]["code"] == -32602


def test_modern_refusals_conform(repo):
    rev = MODERN[0]
    srv = M.Server(repo, agent="conformance")
    bad_version = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                   "params": {"_meta": {P.META_VERSION: "2099-01-01", P.META_CLIENT_CAPS: {}}}}  # fmt: skip
    no_caps = {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
               "params": {"_meta": {P.META_VERSION: rev}}}  # fmt: skip
    bare = {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {}}
    for msg, code in ((bad_version, -32022), (no_caps, -32602), (bare, -32602)):
        reply = srv.handle(msg)
        check_reply(rev, msg["method"], reply)
        assert reply["error"]["code"] == code


# -- multi round-trip (2026-07-28) ---------------------------------------------------------


def test_an_input_required_result_conforms_and_its_retry_is_verified(repo):
    """The engine's MRTR half: a result asking for input, its sealed `requestState`, and
    the retry that echoes it. No tool asks today; the engine can, and checks the echo."""
    rev = MODERN[0]
    params = {
        "name": "ddflow_status",
        "arguments": {},
        "_meta": {P.META_VERSION: rev, P.META_CLIENT_CAPS: {"elicitation": {}}},
    }
    ask = {
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": "Which phase?",
            "requestedSchema": {"type": "object", "properties": {"phase": {"type": "string"}}},
        },
    }
    result = P.input_required("tools/call", params, {"phase": ask}, state={"step": 1})
    reply = P.modernize("tools/call", P.ok(9, result))
    conforms(rev, "CallToolResultResponse", reply)
    conforms(rev, "InputRequiredResult", reply["result"])
    assert reply["result"]["resultType"] == "input_required"

    retry = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {**params, "requestState": result["requestState"],
                   "inputResponses": {"phase": {"action": "accept", "content": {"phase": "P1"}}}},
    }  # fmt: skip
    assert P.modern_check(retry) == rev
    assert P.round_trip(retry["params"], "tools/call") == {"step": 1}
    # The server answers the retry like any modern call: the tool never sees the round trip.
    check_reply(rev, "tools/call", M.Server(repo, agent="conformance").handle(retry))


def _modern_params(**caps):
    return {
        "name": "ddflow_status",
        "arguments": {},
        "_meta": {P.META_VERSION: MODERN[0], P.META_CLIENT_CAPS: caps},
    }


def _retry(params, token, mid=20):
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "method": "tools/call",
        "params": {**params, "requestState": token},
    }


def test_a_state_this_server_did_not_issue_is_refused(repo):
    """`requestState` is attacker-controlled input (MRTR): a forged, tampered, re-targeted
    or expired one is refused with -32602 before anything runs."""
    params = _modern_params()
    token = P.input_required("tools/call", params, state={"n": 1})["requestState"]
    body, tag = token.split(".")
    forged = f"{body}.{tag[:-2]}AA"
    srv = M.Server(repo, agent="conformance")
    for bad in ("garbage", forged, "", f"{body}.{body}"):
        reply = srv.handle(_retry(params, bad))
        check_reply(MODERN[0], "tools/call", reply)
        assert reply["error"]["code"] == -32602, bad
    # issued for ddflow_status, presented on another tool
    other = {**params, "name": "ddflow_board"}
    assert srv.handle(_retry(other, token))["error"]["code"] == -32602
    # issued to one `_meta` agent, presented by another
    someone = {**params, "_meta": {**params["_meta"], P.META_AGENT: "someone-else"}}
    assert srv.handle(_retry(someone, token))["error"]["code"] == -32602
    expired = P.input_required("tools/call", params, state=1, ttl_s=-1)["requestState"]
    assert srv.handle(_retry(params, expired))["error"]["code"] == -32602
    # and the genuine one is served
    assert "result" in srv.handle(_retry(params, token))


def test_input_required_refuses_what_the_spec_forbids():
    params = _modern_params()
    elicit = {"method": "elicitation/create", "params": {"mode": "form", "message": "?",
              "requestedSchema": {"type": "object", "properties": {}}}}  # fmt: skip
    with pytest.raises(ValueError, match="may not answer"):
        P.input_required("tools/list", params, state=1)
    with pytest.raises(ValueError, match="inputRequests, requestState or both"):
        P.input_required("tools/call", params)
    with pytest.raises(ValueError, match="elicitation"):  # the client never declared it
        P.input_required("tools/call", params, {"q": elicit})
    with pytest.raises(ValueError, match="not one a client answers"):
        P.input_required("tools/call", _modern_params(elicitation={}), {"q": {"method": "ping"}})


def test_a_worker_adopts_its_parents_state_key(repo):
    """An offloaded call runs in a worker process (`_offload`): a retry it serves must
    verify against the key of the server that issued the state, so the key rides in the
    job. A worker with a key of its own would refuse every state as forged."""
    import sys

    params = _modern_params()
    mine = P.state_key()
    try:
        parent = P.state_key(b"k" * 32)
        token = P.input_required("tools/call", params, state=1)["requestState"]
    finally:
        P.state_key(mine)
    job = {
        "repo": str(repo),
        "called_from": str(repo),
        "agent": "conformance",
        "msg": _retry(params, token),
        "state_key": parent.hex(),
    }
    run = subprocess.run(
        [sys.executable, "-c", "from ddflow.surfaces.mcp import _worker_main as m; m()"],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[1],
    )
    reply = json.loads(run.stdout.strip().splitlines()[-1])
    assert "result" in reply, reply
