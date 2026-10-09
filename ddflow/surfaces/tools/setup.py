"""MCP tools: setup, configuration, reviewers and reviews.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import review as RV
from ._common import _AGENT_KEYS, _api, _configure_reported

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_setup": {
        "description": (
            "Install ddflow into this repository: creates .ddflow/, writes the driver "
            "and the AGENTS.md section, and registers nothing else. Run this ONCE per "
            "project, then set your test command with ddflow_configure. Safe to re-run "
            "— it updates a managed block and leaves your own prose alone."
        ),
        "properties": {
            "agents": (
                "string",
                # GENERATED from the registry. Hand-kept copies of this list have
                # drifted twice; an agent reads this spec to decide what it may pass.
                "Comma-separated agents to write driver deltas for: "
                f"{','.join(_AGENT_KEYS())}. Default: all.",
                False,
            ),
            "refresh_docs": (
                "boolean",
                "Rewrite ONLY the driver docs, AGENTS.md/CLAUDE.md blocks and adopted agents' native rules from this ddflow's templates (no MCP, hook or command-file changes), e.g. when ddflow_doctor notes drift. Refused on a never-adopted project.",
                False,
            ),
        },
        # Where the server stands decides where the committed files go: a linked
        # worktree's own checkout, not the shared primary (bug B1e7ad10c6c). A foreign
        # `as_agent` is not standing in the connection's tree (B11e4c5a185), so it is
        # asked from the primary, as from a CLI run there -- never the parent's branch.
        "wants_called_from": True,
        "api": lambda repo, a, agent, called_from=None: _api().adopt_project(
            repo,
            _api().Adoption(
                agents=a.get("agents", "") or "", refresh_docs=bool(a.get("refresh_docs"))
            ),
            agent=agent,
            called_from=called_from,
        ),
        # PROSE: a checklist of what it wrote and what to do next.
        "payload": "text",
        "text": True,
        "kind": "setup",
    },
    "ddflow_configure": {
        "description": (
            'Read or write .ddflow/config.toml (WRITES). No arguments: every knob with value, source and meaning. `set` edits one dotted key in place (preferred); `toml` APPENDS a fragment, e.g.\n[gate.unit_tests]\ncommand = "pytest -q -n auto"\n(needs pytest-xdist). The committed file is generic project policy; anything of THIS machine or operator (a reviewer endpoint, a host, a key variable, worker counts) goes with local=true to the git-ignored .ddflow/local/config.toml, read last and never committed. A reviewer there is a `[[reviewer]]` block; `ddflow_reviewers_detect` write=true writes one.'
        ),
        "properties": {
            "set": (
                "string",
                "Dotted key to set, e.g. 'gate.unit_tests.command'. Preferred: it "
                "edits in place and works whether or not the section exists.",
                False,
            ),
            "value": ("string", "The value for `set`.", False),
            "toml": (
                "string",
                "A whole TOML block to append. Fails if it would "
                "duplicate an existing table — use `set` instead then.",
                False,
            ),
            "filter": ("string", "Only show knobs whose name contains this.", False),
            "local": (
                "boolean",
                "Write `set`/`toml` to the git-ignored .ddflow/local/config.toml instead "
                "of the committed config: for this machine's endpoints, hosts, key "
                "variables and sizing.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _configure_reported(repo, a, agent),  # noqa: PLW0108 -- defined below the table
        # PROSE, and `explain=True`: the string path was `config --explain`, which is
        # every knob with its documentation AND its source. The source is the half an
        # operator debugging a setting cannot do without.
        "payload": "text",
        "text": True,
        "kind": "config",
    },
    "ddflow_reviewers_detect": RV.BY_TOOL["ddflow_reviewers_detect"].tool_entry(),
    "ddflow_reviewers_list": RV.BY_TOOL["ddflow_reviewers_list"].tool_entry(),
    "ddflow_review": RV.BY_TOOL["ddflow_review"].tool_entry(),
    "ddflow_review_triage": RV.BY_TOOL["ddflow_review_triage"].tool_entry(),
}
