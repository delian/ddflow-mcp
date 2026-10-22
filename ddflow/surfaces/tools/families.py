"""Tool families: the 24 tools a diet client sees, generated from the declared matrix.

D-mcp-tool-families: 9 hot single tools plus 15 noun families, each served by one tool with
an ``action`` enum named like the CLI's ``noun verb``. Nothing here lists a tool twice: a
declared `Command` carries its own ``family`` and ``action``; the tools declared before the
fields existed (and the hand-written ones) are placed by `_LEGACY` -- a frozen table in the
manner of `tiers._LEGACY_*` -- and import fails for a tool that has neither, so a new tool
cannot land outside a family. `FAMILIES` and `ALIASES` are computed from that placement.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from . import DECLARED, TOOLS

PREFIX = "ddflow_"

#: The most-called verbs keep their own direct tool (family == the tool, no action).
HOT_SINGLES = tuple(
    PREFIX + n for n in "identify brief next claim heartbeat complete merge show help".split()
)

#: Legacy placement: family noun -> the tool-name suffixes (after ``ddflow_``) it serves.
_LEGACY: dict[str, str] = {
    "gate": "gate_list gate_record gate_run gate_skip gate_status gate_verify ci tests",
    "bug": "bug_file_tasks bug_fixed bug_found bug_invalid",
    "decision": "decision_add decision_applicable decision_list decision_show decision_supersede",
    "memory": (
        "memory_add memory_forget memory_list lesson_add lesson_search lesson_verify recall "
        "link similar dupes"
    ),
    "rule": "rule_add rule_edit rule_list rule_remove rule_search rule_show rule_sync",
    "session": "session_end session_note session_prompt session_start",
    "job": "job_add job_end job_list job_run",
    "ship": "promote_add promote_deployed promote_status version_cut version_show",
    "workflow": "workflow workflow_drop workflow_gate workflow_pipeline workflow_state flow_choose flow_show",
    "reviewers": (
        "review review_triage reviewers_detect reviewers_list companions companions_add "
        "companions_verify pr_status pr_sync pr_threads"
    ),
    "task": (
        "task_add task_fold update phase_add phase_close research_add resolve split remove "
        "rename batch"
    ),
    "lease": "abandon block unblock release wait",
    "view": (
        "board cadence docs health history lanes list loops metrics pins progress queue "
        "render status"
    ),
    "doctor": "doctor bisect cleanup hooks precommit rebuild record recover replay verify",
    "admin": (
        "configure export external_sync import import_verify onboard prompts setup sync upgrade"
    ),
}

#: The 15 family tools; a declared `Command.family` must be one of them (a typo is not a family).
FAMILY_TOOLS = tuple(PREFIX + noun for noun in _LEGACY)

#: A tool whose name IS its family noun has no verb to strip: the action says what it does.
_SELF_NAMED_ACTION = {"doctor": "check", "workflow": "show"}


class Placement(NamedTuple):
    family: str
    action: str


class FamilyAlias(NamedTuple):
    """An old tool name and the family tool and action that now serves it."""

    old: str
    family: str
    action: str


class Family(NamedTuple):
    """One advertised tool: a family with its ``action -> legacy tool`` members, or a single."""

    name: str
    members: Mapping[str, str]
    single: bool


def _legacy_placements() -> dict[str, Placement]:
    placed: dict[str, Placement] = {}
    for noun, suffixes in _LEGACY.items():
        for suffix in suffixes.split():
            if suffix == noun:
                action = _SELF_NAMED_ACTION[noun]
            else:
                action = suffix.removeprefix(noun + "_")
            placed[PREFIX + suffix] = Placement(PREFIX + noun, action)
    return placed


def resolve(
    commands: Mapping[str, Any],
    legacy: Mapping[str, Placement],
    names: Any = None,
) -> dict[str, Placement]:
    """Every tool's family and action: its own declaration first, else the legacy table.

    Raises `RuntimeError` naming each tool with neither, or with a family and no action.
    """
    placed: dict[str, Placement] = {}
    bad: list[str] = []
    for name in names if names is not None else commands:
        cmd = commands.get(name)
        family = getattr(cmd, "family", "")
        action = getattr(cmd, "action", "")
        if name in HOT_SINGLES and not (family or action):
            placed[name] = Placement(name, "")
        elif name in HOT_SINGLES or family in HOT_SINGLES:
            bad.append(f"{name}: a hot single takes no family or action ({family or name})")
        elif family and family not in FAMILY_TOOLS:
            bad.append(f"{name}: family {family} is not one of {', '.join(FAMILY_TOOLS)}")
        elif family and action:
            placed[name] = Placement(family, action)
        elif family or action:
            bad.append(f"{name}: a family and an action are declared together or not at all")
        elif name in legacy:
            placed[name] = legacy[name]
        else:
            bad.append(f"{name}: no `Command.family`/`action` and not in the legacy table")
    if bad:
        raise RuntimeError("tools placed in no family (action required): " + "; ".join(bad))
    return placed


_PLACED = resolve(DECLARED, _legacy_placements(), TOOLS)


def placement(tool: str) -> Placement:
    """The family tool and action serving ``tool`` (a hot single serves itself, action "")."""
    return _PLACED[tool]


def build(placed: Mapping[str, Placement]) -> dict[str, Family]:
    """The families of a placement; two tools sharing one action of a family is an error."""
    members: dict[str, dict[str, str]] = {}
    for tool, (family, action) in placed.items():
        if members.setdefault(family, {}).setdefault(action, tool) != tool:
            raise RuntimeError(f"{family}: action {action!r} is served by two tools")
    return {f: Family(f, m, single=f in HOT_SINGLES) for f, m in members.items()}


#: Family tool name -> `Family`; the 9 singles and the 15 families, in tool order.
FAMILIES: dict[str, Family] = build(_PLACED)

#: Every old name that is not a hot single, with where it went (for MTD-aliases).
ALIASES: tuple[FamilyAlias, ...] = tuple(
    FamilyAlias(t, p.family, p.action) for t, p in _PLACED.items() if t not in HOT_SINGLES
)
