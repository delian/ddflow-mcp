"""The MCP tool list is paid for in every agent's context on every session, so its size is
ratcheted. Measured before the trim: 90 tools, 119,179 bytes compact. The shared `as_agent`
/ `relation` / `check_only` descriptions were repeated in full on 89 / 7 / 7 tools; the full
text now lives once (ddflow_identify, ddflow_help and the handshake instructions)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from ddflow.config import MCP_TOOL_TIERS
from ddflow.surfaces import mcp as mcp_module
from ddflow.surfaces.mcp import ADD_TOOLS, TOOLS, Server, _schema

#: Compact `tools/list` bytes. Raise only with a reason in the commit; lowering is welcome.
# 93_500: ddflow_export (B-export-surfaces) is ~1.4 KB, 15 arguments because the CLI/MCP flag
# parity ratchet needs every `ddflow export` filter flag reachable; main sat at 91,958.
# 91_500: ddflow_list (B-view-mcp-list) is ONE tool for the six viewers (~1.3 KB); the
# shared `as_agent` description, repeated on every tool, went from 113 to 66 characters (-4 KB).
# 93_500 (raised, B-dupes-sweep): ddflow_dupes + ddflow_link settle near-duplicate pairs
# already in the log; together ~1.3 KB. The cheaper alternative (one tool with a mode
# argument) would still carry most of the text and need the same flag exemptions.
# 94_500 (raised, B-upgrade.3-plan): ddflow_upgrade is the MCP face of `ddflow upgrade --plan`
# (~0.7 KB): one tool with one optional argument; main sat at 93,4xx.
# 95_000 (raised, B-gate-econ-review-refutation.2-surfaces): ddflow_gate_list is the MCP face of
# `ddflow gate list [--refuted]` (~0.5 KB): one tool, one optional argument; the list measures 94,598 bytes with it.
# 95_300 (raised, B-upgrade.4-apply.3b-wire): ddflow_upgrade gains `snapshot` and `restore`
# and a longer description (~0.3 KB in all); the list measures 95,2xx with them.
# 95_700 (raised, B-uni-rules-import.4-view): ddflow_rule_sync is the MCP face of `ddflow rule sync`
# (~0.3 KB): one tool, no arguments.
# 95_800 (raised, B-upgrade.8-release-check): ddflow_upgrade gains `check` (~0.1 KB net after
# trimming its description); the list measures 95,773 with it.
# 96_000 (raised, B-upgrade.9-self-upgrade): ddflow_upgrade gains `self` and `to` (~0.2 KB): the
# MCP face of `ddflow upgrade --self`, with consent carried by the existing confirm/reason; the list
# measures 95,987 with them.
# 96_800 (raised, fix-import-leaves-finished-phases-open): ddflow_phase_close is the MCP face of
# `ddflow phase close <id> --imported` (~0.7 KB): one tool, one optional argument, which closes a
# phase whose work predates ddflow without running the phase pipeline. main sat at 95,991 with 9
# bytes free, so the tool had to be paid for; its description is already down to the essentials.
# 97_400 (raised, B-gap-lanes): ddflow_lanes is the MCP face of `ddflow lanes` (~0.6 KB: one
# tool, three arguments, a short description); the list measures 97,394 with it and phase_close.
# Since D-mcp-budget-report this line is REPORTED (a warning), not enforced; only core is ratcheted.
# 100_000 (RAISED BY THE OPERATOR, 2026-10-12, mcp-tools-budget-100k): the list sat at 97,394
# with 6 bytes free, so every new tool cost a reasoned raise of its own -- 96_000 to 96_800 to
# 97_400 in two days. The operator asked for headroom instead of that round per tool. This is
# NOT a target to grow into: shortening a description is still the first move when a tool
# pushes the list up, and the check still fails loudly past 100,000. D-tools-list-budget-100k.
TOOLS_LIST_BUDGET = 100_000
SHARED_DESCRIPTION_MAX = 200


def _tools_list(repo) -> list[dict]:
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return reply["result"]["tools"]


#: The one tier a session pays for at start, so the one that keeps a ratchet (D-mcp-budget-report).
CORE_BUDGET = 40_000


def tier_sizes(repo, monkeypatch) -> dict[str, int]:
    """Compact `tools/list` bytes of every tier."""
    sizes = {}
    for tier in MCP_TOOL_TIERS:
        monkeypatch.setenv("DDFLOW_MCP_TOOLS", tier)
        sizes[tier] = len(json.dumps({"tools": _tools_list(repo)}, separators=(",", ":")))
    return sizes


def budget_findings(sizes: dict[str, int]) -> tuple[list[str], list[str]]:
    """(failures, warnings): only the core tier over its ratchet fails; a tier or the full list
    over `TOOLS_LIST_BUDGET` is a reported number (D-mcp-budget-report)."""
    report = ", ".join(f"{t}={n}" for t, n in sizes.items())
    fails = [f"core tools/list is {sizes['core']} bytes (ratchet {CORE_BUDGET}); {report}"]
    fails = fails if sizes["core"] > CORE_BUDGET else []
    warns = [
        f"{t} tools/list is {n} bytes, over the {TOOLS_LIST_BUDGET} report line ({report})"
        for t, n in sizes.items()
        if n > TOOLS_LIST_BUDGET
    ]
    return fails, warns


def test_over_budget_is_reported_not_failed_and_only_core_has_a_ratchet():
    fails, warns = budget_findings({"core": 30_000, "standard": 60_000, "all": 130_000})
    assert fails == [] and len(warns) == 1 and "all tools/list" in warns[0]
    fails, _ = budget_findings({"core": CORE_BUDGET + 1, "standard": 60_000, "all": 70_000})
    assert len(fails) == 1 and "core" in fails[0]


def test_tools_list_bytes_per_tier_are_reported(repo, monkeypatch):
    sizes = tier_sizes(repo, monkeypatch)
    fails, warns = budget_findings(sizes)
    print("tools/list bytes per tier:", sizes)  # shown by `pytest -s` and on any failure
    for w in warns:
        warnings.warn(w, stacklevel=1)
    assert not fails, fails


def test_shared_parameter_descriptions_are_not_repeated_in_full():
    long_ones = []
    for name, spec in TOOLS.items():
        props = _schema(spec)["properties"]
        for arg in ("as_agent", "relation", "check_only"):
            if arg in props and len(props[arg]["description"]) > SHARED_DESCRIPTION_MAX:
                long_ones.append((name, arg))
    assert not long_ones, long_ones
    # ...and the add tools still carry the answer arguments at all.
    for name in ADD_TOOLS:
        props = _schema(TOOLS[name])["properties"]
        assert "relation" in props and "check_only" in props, name


def test_the_trimmed_text_still_has_a_home():
    """Shortening must not delete the semantics: `as_agent` is explained on
    ddflow_identify, and the relation values in the handshake instructions."""
    ident = TOOLS["ddflow_identify"]["description"]
    assert "as_agent" in ident and "--agent" in ident
    template = Path(mcp_module.__file__).parent.parent / "templates/prompts/mcp_instructions.md"
    text = template.read_text()
    for value in ("extends:ID", "duplicate_of:ID", "related:ID"):
        assert value in text, value


def test_ddflow_loops_names_the_repeated_failure_detector():
    """B1d72a8144c: repeated_failure was missing from the tools/list description."""
    assert "repeated_failure" in TOOLS["ddflow_loops"]["description"]
