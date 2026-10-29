"""Descriptor v2: strict, three-valued, backward compatible."""

from __future__ import annotations

import pytest

from ddflow.services import harnessreg
from ddflow.services.harnessreg import DescriptorError, parse

BASE = """
id = "acme"
rank = 1
family = "acme"
delta = "acme.md"
[mcp]
path = ".acme/mcp.json"
shape = "mcpServers"
[verified]
date = "2026-10-29"
sources = ["https://example.com"]
"""


def p(extra: str = ""):
    return parse(BASE + extra, "acme")


def test_shipped_descriptors_still_load_with_v2_not_verified():
    for h in harnessreg.load():
        assert h.detect is None and h.headless is None and h.autonomy is None
        assert h.reserved == {}


def test_all_v2_sections_parse():
    h = p(
        """
[detect]
client_names = ["acme-cli"]
env = ["ACME_SESSION"]
[headless]
argv = ["acme", "-p"]
model_flag = "--model"
autonomy = "yolo"
exit_codes = { "1" = "error" }
[autonomy]
profiles = { yolo = "--yes" }
files = { ".acme/p.json" = "permissions" }
[continuation]
mechanism = "stop-hook-block"
cap = 5
[meters]
context = "statusline"
[caps]
mcp_tools = 128
[telemetry]
transcript_path = "~/.acme/t.jsonl"
[spawn]
none = true
[test]
cmd = "acme -p hi"
"""
    )
    assert h.detect.client_names == ("acme-cli",)
    assert h.headless.exit_codes == {"1": "error"} and h.caps.mcp_tools == 128
    assert h.continuation.cap == 5 and h.reserved["spawn"].none
    assert h.reserved["test"].values == {"cmd": "acme -p hi"}


def test_falsy_value_is_still_a_fact():
    assert p("[caps]\nsubagent_depth = 0\n").caps.subagent_depth == 0


def test_none_is_documented_absence_and_absent_is_not_verified():
    h = p("[continuation]\nnone = true\n")
    assert h.continuation.none is True and h.caps is None


@pytest.mark.parametrize(
    "extra, needle",
    [
        ("[caps]\nmcp_tool = 3\n", r"unknown key\(s\) mcp_tool"),
        ("[caps]\nmcp_tools = '3'\n", "expected int"),
        ("[caps]\nmcp_tools = -1\n", "negative"),
        ("[caps]\n", "empty"),
        ("[caps]\nnone = true\nsubagent_depth = 0\n", "none = true and also"),
        ("[continuation]\nmechanism = 'none'\ncap = 10\n", "need a mechanism"),
        ("[continuation]\ncap = 0\n", "need a mechanism"),
        ("[caps]\nnone = true\nmcp_tools = 3\n", "none = true and also"),
        ("[continuation]\nmechanism = 'magic'\n", "continuation.mechanism"),
        ("[continuation]\ncap = 3\n", "need a mechanism"),
        ("[headless]\nautonomy = 'yolo'\n", "headless.autonomy"),
        ("[spawn]\n", "exactly one"),
        ("[spawn]\nnone = true\nx = 'y'\n", "exactly one"),
        ("[bot]\nn = 3\n", r"bot\]\.n"),
        ("[nonsense]\nx = 1\n", "unknown top-level"),
    ],
)
def test_strict_errors_name_the_key(extra, needle):
    with pytest.raises(DescriptorError, match=needle):
        p(extra)
