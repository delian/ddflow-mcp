"""Tool tiers: which tools `tools/list` advertises under `[mcp].tools`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import DECLARED, TOOLS
from .profiles import AUTO, DEFAULT_PROFILE, PROFILES, direct_tools, family_tools, profile_note

# -- tool tiers -------------------------------------------------------------------------
#
# `[mcp].tools = core | standard | all` (D-lean-and-trusted (4)) decides which tools
# `tools/list` ADVERTISES. It is a start-time knob and nothing more: `TOOLS` stays the one
# registry, every tool stays callable by name whatever the tier, and `listChanged` stays
# false. A client that loads every schema up front pays for what is advertised, so the tier
# is a context-cost choice and never a permission.
#
# ONE data structure, three disjoint sets that together cover `TOOLS` exactly. The legacy
# tables below are frozen; a new tool places itself with `Command.tier` (import fails on an
# empty one) rather than silently landing in whichever tier nobody thought about. Measured on this tree: core
# 39 KB, standard 67 KB, all 92 KB of compact `tools/list`.

#: The daily loop: pick work, claim it, satisfy gates, record what you learn, land it.
#: `setup` is here because the handshake of an unadopted repository tells the agent to call
#: it before anything else.
_LEGACY_CORE = frozenset(
    "ddflow_" + n
    for n in (
        "brief next claim heartbeat gate_status gate_run gate_record gate_skip gate_verify "
        "complete merge status show recall bug_found bug_fixed bug_invalid lesson_add "
        "decision_add session_start session_end session_note session_prompt identify "
        "task_add update wait review help pr_sync similar setup "
        # the charter's own tools (B7752a21d68): it tells every agent to read any rule through these.
        # The flat `core` tier only: the default LEAN profile reaches them through the `rule` family
        # (profiles.py). release and review_triage stay in `standard`: only the role definitions name
        # them, and those must name tools per profile (follow-up fix-B07c4286c92).
        "rule_show rule_search"
    ).split()
)

#: Commonly used beyond the loop: reading the queue and memory, reshaping work, config.
_LEGACY_STANDARD = frozenset(
    "ddflow_" + n
    for n in (
        "abandon block unblock release board progress doctor recover configure companions "
        "decision_applicable decision_list decision_show decision_supersede lesson_search "
        "flow_show pr_status pr_threads reviewers_list version_show research_add phase_add split resolve "
        "remove tests review_triage memory_add memory_list history cleanup render list "
        "rule_add rule_list rule_edit rule_remove rule_sync workflow_state verify ci onboard "
        "dupes link gate_list queue"
    ).split()
)

#: Administration and rarely used capabilities: only in `all`.
_LEGACY_FULL = frozenset(
    "ddflow_" + n
    for n in (
        "external_sync import import_verify job_add job_end job_list job_run promote_add promote_deployed "
        "promote_status workflow workflow_drop workflow_gate workflow_pipeline flow_choose "
        "version_cut upgrade rebuild replay hooks precommit companions_add companions_verify bisect prompts pins loops cadence lanes metrics health record "
        "reviewers_detect memory_forget lesson_verify export bug_file_tasks phase_close docs"
    ).split()
)

TIERS = ("core", "standard", "all")


def _declared(tier: str) -> frozenset[str]:
    """The tools outside the legacy sets that declare themselves into `tier`."""
    legacy = _LEGACY_CORE | _LEGACY_STANDARD | _LEGACY_FULL
    bad = {n: c.tier for n, c in DECLARED.items() if n not in legacy and c.tier not in TIERS}
    if bad:
        raise RuntimeError(f"tools with no valid `Command.tier` (one of {TIERS}): {bad}")
    return frozenset(n for n, c in DECLARED.items() if n not in legacy and c.tier == tier)


#: The three disjoint sets: the legacy tables above, plus each tool's own declared tier.
CORE_TOOLS = _LEGACY_CORE | _declared("core")
STANDARD_EXTRA_TOOLS = _LEGACY_STANDARD | _declared("standard")
FULL_ONLY_TOOLS = _LEGACY_FULL | _declared("all")
DEFAULT_TIER = "all"


def tier_tools(tier: str) -> frozenset[str]:
    """The tools of `TOOLS` that `tools/list` advertises at `tier` by their own name; an unknown
    tier advertises everything. A profile also lists family tools, which are not in `TOOLS`."""
    if tier == AUTO:
        tier = DEFAULT_PROFILE
    if tier in PROFILES:
        return frozenset(direct_tools(tier))
    if tier == "core":
        return CORE_TOOLS
    if tier == "standard":
        return CORE_TOOLS | STANDARD_EXTRA_TOOLS
    return frozenset(TOOLS)


def _config(repo: Path) -> Any:
    """The repository's config, or None when it cannot be read at all."""
    try:
        from ...config import Config

        return Config.load(repo)
    except Exception:
        return None


def resolve_tier(repo: Path) -> str:
    """`[mcp].tools` for this repository (env `DDFLOW_MCP_TOOLS` wins), read once.

    Forward-compatible by construction: a value this version does not know (a newer
    release's tier in a shared config) advertises EVERYTHING rather than failing the handshake;
    a config that cannot be read at all is the default (`auto`: the lean core). Over-listing costs context; under-listing
    would hide a tool the agent needs.
    """
    cfg = _config(repo)
    #: A config that cannot be read is the default (`auto`); a value this version does not know
    #: is the newer release's, and lists everything.
    tier = (cfg.mcp.tools if cfg is not None else AUTO).strip().lower() or AUTO
    return tier if tier in (*TIERS, *PROFILES, AUTO) else DEFAULT_TIER


def resolve_output_schemas(repo: Path) -> bool:
    """`[mcp].output_schemas` for this repository, read once: on or off. A value this version
    does not know, or a config that cannot be read, is off -- the smaller, unchanged list."""
    cfg = _config(repo)
    return cfg is not None and cfg.mcp.output_schemas == "on"


def tier_note(tier: str, *, names: bool = True) -> str:
    """What a tier hides and how to widen it. `ddflow_help` carries the names; the handshake
    (paid for on every session) only counts them and points at `ddflow_help`."""
    if tier == AUTO:
        tier = DEFAULT_PROFILE
    if tier in PROFILES:
        return profile_note(tier, len(set(TOOLS) | set(family_tools("families"))))
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


# Loading the tiers (every MCP server does) runs the family check: a tool with no family and
# action fails import here, not in some later release (MTD-family-spec, D-mcp-tool-families).
from . import families  # noqa: E402,F401
