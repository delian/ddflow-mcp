"""MCP tools: onboard and identify (hand-written; the rest are declared).

Hand-written entries merged into `TOOLS` in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..registry import Alias
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_onboard": {
        "description": (
            "The onboarding stages in one call: status (standing drift report), preflight, legacy, memory, test-gate, verify, harness (per-agent: registered?, approve, hooks, best ladder level). Propose by default; apply=true acts on the safe/approved items and accepts names."
        ),
        "properties": {
            "stage": (
                "string",
                "A stage name: status (the drift report, default), preflight, legacy, memory, test-gate, verify or harness (accept = agent ids).",
                False,
            ),
            "apply": (
                "boolean",
                "Act on the proposal (preflight/legacy/memory/harness: approve, hooks).",
                False,
            ),
            "accept": (
                "array",
                "Names to approve (preflight leftovers, memory facts); empty = everything the report marked safe.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().onboard_run(
            repo,
            stage=str(a.get("stage") or "status"),
            apply=bool(a.get("apply")),
            accept=tuple(a.get("accept") or ()),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_identify": {
        "description": (
            "Declare WHO you are on this connection before anything that writes. Call it first when 2+ agents or subagents work this repository at once: identity attributes every claim, gate outcome and review, and the tree-derived default merges several agents in one tree into one identity with no error (a review would pass independence against itself). Pick a short stable name (your role), distinct from the others'. Idempotent. A SUBAGENT sharing its parent's connection must NOT call this; it passes `as_agent` on each call instead (the CLI's `--agent`). Stateless 2026-07-28 requests: persists nothing; name yourself per call (as_agent or _meta ddflow/agent)."
        ),
        "properties": {
            "agent": (
                "string",
                "A short stable name, e.g. 'reviewer-2' (letters, digits, . _ -; max 64; it names your log file). OMIT to reset to the tree-derived default.",
                False,
            ),
        },
        "identify": True,
        # Callers lost calls to `name`; it still works (D-compat).
        "arg_aliases": {"name": Alias("argument", "name", "agent", "0.2.3")},
    },
}
