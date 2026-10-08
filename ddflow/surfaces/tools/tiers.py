"""Tool tiers: which tools `tools/list` advertises under `[mcp].tools`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import TOOLS

# -- tool tiers -------------------------------------------------------------------------
#
# `[mcp].tools = core | standard | all` (D-lean-and-trusted (4)) decides which tools
# `tools/list` ADVERTISES. It is a start-time knob and nothing more: `TOOLS` stays the one
# registry, every tool stays callable by name whatever the tier, and `listChanged` stays
# false. A client that loads every schema up front pays for what is advertised, so the tier
# is a context-cost choice and never a permission.
#
# ONE data structure, three disjoint sets that together cover `TOOLS` exactly. A new tool
# therefore has to be placed (`tests/test_mcp_tool_tiers.py` fails until it is), rather
# than silently landing in whichever tier nobody thought about. Measured on this tree: core
# 39 KB, standard 67 KB, all 92 KB of compact `tools/list`.

#: The daily loop: pick work, claim it, satisfy gates, record what you learn, land it.
#: `setup` is here because the handshake of an unadopted repository tells the agent to call
#: it before anything else.
CORE_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "brief next claim heartbeat gate_status gate_run gate_record gate_skip gate_verify "
        "complete merge status show recall bug_found bug_fixed bug_invalid lesson_add "
        "decision_add session_start session_end session_note session_prompt identify "
        "task_add update wait review help pr_sync similar setup"
    ).split()
)

#: Commonly used beyond the loop: reading the queue and memory, reshaping work, config.
STANDARD_EXTRA_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "abandon block unblock release board progress doctor recover configure companions "
        "decision_applicable decision_list decision_show decision_supersede lesson_search "
        "flow_show pr_status pr_threads reviewers_list version_show research_add phase_add split resolve "
        "remove tests review_triage memory_add memory_list history cleanup render list "
        "rule_add rule_list rule_search rule_edit rule_remove rule_show workflow_state verify ci onboard "
        "dupes link gate_list"
    ).split()
)

#: Administration and rarely used capabilities: only in `all`.
FULL_ONLY_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "external_sync import import_verify job_add job_end job_list job_run promote_add promote_deployed "
        "promote_status workflow workflow_drop workflow_gate workflow_pipeline flow_choose "
        "version_cut upgrade rebuild replay hooks precommit companions_add companions_verify bisect prompts pins loops cadence "
        "reviewers_detect memory_forget lesson_verify export bug_file_tasks"
    ).split()
)

TIERS = ("core", "standard", "all")
DEFAULT_TIER = "all"


def tier_tools(tier: str) -> frozenset[str]:
    """The tools `tools/list` advertises at `tier`; an unknown tier advertises everything."""
    if tier == "core":
        return CORE_TOOLS
    if tier == "standard":
        return CORE_TOOLS | STANDARD_EXTRA_TOOLS
    return frozenset(TOOLS)


def _mcp_config(repo: Path) -> Any:
    """The repository's `[mcp]` section, or None when the config cannot be read at all."""
    try:
        from ...config import Config

        return Config.load(repo).mcp
    except Exception:
        return None


def resolve_tier(repo: Path) -> str:
    """`[mcp].tools` for this repository (env `DDFLOW_MCP_TOOLS` wins), read once.

    Forward-compatible by construction: a value this version does not know (a newer
    release's tier in a shared config), or a config that cannot be read at all, advertises
    EVERYTHING rather than failing the handshake. Over-listing costs context; under-listing
    would hide a tool the agent needs.
    """
    mcp = _mcp_config(repo)
    tier = mcp.tools if mcp is not None else DEFAULT_TIER
    return tier if tier in TIERS else DEFAULT_TIER


def resolve_output_schemas(repo: Path) -> bool:
    """`[mcp].output_schemas` for this repository, read once: on or off. A value this version
    does not know, or a config that cannot be read, is off -- the smaller, unchanged list."""
    mcp = _mcp_config(repo)
    return mcp is not None and mcp.output_schemas == "on"


def tier_note(tier: str, *, names: bool = True) -> str:
    """What a tier hides and how to widen it. `ddflow_help` carries the names; the handshake
    (paid for on every session) only counts them and points at `ddflow_help`."""
    if tier not in TIERS or tier == "all":
        return ""
    hidden = sorted(set(TOOLS) - tier_tools(tier))
    wider = "`standard` or `all`" if tier == "core" else "`all`"
    head = (
        f"This connection lists the `{tier}` tool tier ({len(TOOLS) - len(hidden)} of "
        f"{len(TOOLS)} tools). The other {len(hidden)} are NOT in `tools/list` but are "
        f"still callable by name with the same arguments"
    )
    listed = f": {', '.join(hidden)}" if names else " (`ddflow_help` names them)"
    return (
        f"{head}{listed}. To list "
        f"them, set `[mcp].tools` to {wider} in .ddflow/config.toml (or "
        f"`DDFLOW_MCP_TOOLS`) and restart the server; the tier is read once at start."
    )
