"""One RESOURCES table for the MCP resources (B-uni-cmd-migrate.3-resources).

`resources/list` and `resources/read` used to be two literals that had to agree (a dead entry
in one, shadowed by the other, is how a second path hides). Both now come from `RESOURCES`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.surfaces import mcp as MCP
from ddflow.surfaces.mcp import RESOURCE_BY_URI, RESOURCES, Server

URIS = [
    "ddflow://board",
    "ddflow://brief",
    "ddflow://lessons",
    "ddflow://lessons-summary",
    "ddflow://research",
    "ddflow://bugs",
    "ddflow://decisions",
    "ddflow://sessions",
]


def _call(repo, method, **params):
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return Server(repo).handle(msg)


def test_the_listing_is_the_table_in_order(repo):
    listed = _call(repo, "resources/list")["result"]["resources"]
    assert [r["uri"] for r in listed] == URIS == [r.uri for r in RESOURCES]
    assert all(set(r) == {"uri", "name", "description", "mimeType"} for r in listed)
    assert {r["mimeType"] for r in listed} == {"text/markdown"}


def test_every_listed_resource_reads_and_an_unknown_one_is_an_invalid_param(repo):
    for uri in URIS:
        got = _call(repo, "resources/read", uri=uri)["result"]["contents"]
        assert got[0]["uri"] == uri and isinstance(got[0]["text"], str), uri
    for uri in ("ddflow://nope", ""):
        reply = _call(repo, "resources/read", uri=uri)
        assert reply["error"]["code"] == -32602 and repr(uri) in reply["error"]["message"]


def test_the_table_is_the_only_place_that_names_a_resource():
    source = Path(MCP.__file__).read_text("utf-8")
    assert len(re.findall(r'"ddflow://', source)) == len(RESOURCES) == len(RESOURCE_BY_URI)
