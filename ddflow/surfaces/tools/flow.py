"""MCP tools: pull requests, versions, promotion and the branching flow.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_pr_sync": {
        "description": (
            "Ask the forge (GitHub/GitLab) what reviewers did with every request in REVIEW and record it: a merged request completes its item (and retargets what is stacked on it), requested changes return the item to the queue WITH the review text, a closed one is parked for a person, an approved green one is merged when [flow].pr_merge = 'on_approval'. `ddflow_next` does this itself with [flow].sync_on_next. Exit 2: the forge could not be asked -- NOT 'nothing changed'."
        ),
        "properties": {"item": ("string", "Only this item (optional).", False)},
        "api": lambda repo, a, agent: _api().pr_sync(
            repo, item=a.get("item", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_pr_status": {
        "description": (
            "Every item's pull request as last recorded — review, checks, target, rounds "
            "of changes and when it was last looked at. Reads the log only; "
            "`ddflow_pr_sync` asks the forge."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().pr_status(repo, agent=agent),
        "payload": "",
    },
    "ddflow_pr_threads": {
        "description": (
            "An item's review threads, read live from the forge. With `thread`, `reply` on "
            "it and/or `resolve` it, so the reviewer sees what was addressed. Exit 2 = "
            "forge not reached; 3 = refused."
        ),
        "properties": {
            "id": ("string", "The item.", True),
            "thread": ("string", "Thread id, as listed.", False),
            "reply": ("string", "Reply text.", False),
            "resolve": ("boolean", "Resolve it.", False),
        },
        "api": lambda repo, a, agent: _api().pr_threads(
            repo,
            a["id"],
            thread=a.get("thread", "") or "",
            reply=a.get("reply", "") or "",
            resolve=bool(a.get("resolve")),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_version_show": {
        "description": (
            "The current version (highest `<tag_prefix>X.Y.Z` tag reachable from the "
            "release branch), the next one, the bump and why (Conventional Commits plus "
            "the tags of finished items), and the release notes. Reads only."
        ),
        "properties": {
            "bump": ("string", "Force major | minor | patch instead of the computed bump.", False),
            "line": ("string", "A maintenance line (default: the current one).", False),
        },
        "api": lambda repo, a, agent: _api().version_show(
            repo, bump=a.get("bump", "") or "", line=a.get("line", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_version_cut": {
        "description": (
            "Tag the next version. trunk: tags the base branch. gitflow: release/X from "
            "develop, merged to production, tagged, tag merged back; with pull requests, "
            "opens the release request (`ddflow_pr_sync` tags it once merged). Exit 2 = "
            "nothing to release."
        ),
        "properties": {
            "bump": ("string", "major | minor | patch.", False),
            "version": ("string", "MAJOR.MINOR.PATCH.", False),
            "push": ("boolean", "Publish the tag and branches.", False),
            "dry_run": ("boolean", "Report only.", False),
            "line": (
                "string",
                "A maintenance line: tagged where it stands; its major is kept.",
                False,
            ),
            "changelog": (
                "boolean",
                "Also write CHANGELOG.md.",
                False,
            ),
            "force": ("boolean", "Overwrite a hand-edited file.", False),
        },
        "api": lambda repo, a, agent: _api().version_cut(
            repo,
            bump=a.get("bump", "") or "",
            version=a.get("version", "") or "",
            push=bool(a.get("push")),
            dry_run=bool(a.get("dry_run")),
            line=a.get("line", "") or "",
            changelog=bool(a.get("changelog")),
            force=bool(a.get("force")),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_promote_add": {
        "description": (
            "File a PROMOTION to an environment branch ([flow].environments): a task that "
            "merges the branch immediately upstream into it, runs the promotion pipeline "
            "and lands by merge or request -- always one step downstream. Exit 2 = nothing "
            "to promote; exit 3 = refused (unknown environment, one open, branch missing)."
        ),
        "properties": {
            "env": ("string", "The environment to promote TO.", True),
            "force": ("boolean", "File it even with nothing to carry.", False),
        },
        "api": lambda repo, a, agent: _api().promote_add(
            repo, a["env"], force=bool(a.get("force")), agent=agent
        ),
        "payload": "",
    },
    "ddflow_promote_deployed": {
        "description": (
            "Record the sha a deploy put LIVE in an environment (from the deploy hook); "
            "promote_status then shows what runs there. Exit 3 = refused."
        ),
        "properties": {
            "env": ("string", "Environment.", True),
            "sha": (
                "string",
                "Deployed commit (default: branch head).",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().promote_deployed(
            repo, a["env"], sha=a.get("sha", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_promote_status": {
        "description": (
            "Each environment: head, commits behind its upstream, open promotion, "
            "auto_promote, and the live (deployed) sha. Reads only."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().promote_status(repo, agent=agent),
        "payload": "",
    },
    "ddflow_flow_show": {
        "description": (
            "How THIS project works: its branching model, release lines, and every workflow "
            "choice (model, integration, pr_merge, port_strategy, ...) with its value, the "
            "options, and who decided -- the operator's config, a recorded choice, or a "
            "default nobody chose. `pending` lists relevant choices nobody has made: ask the "
            "operator, or pick what suits the project with `ddflow_flow_choose`."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().flow_show(repo, agent=agent),
        "payload": "",
    },
    "ddflow_flow_choose": {
        "description": (
            "Record a workflow choice for this project, attributed to you, with a reason the "
            "next agent will read. Make it when the operator told you, or when they left it "
            "to you -- a choice left unmade is defaulted at first use and followed from then "
            "on. The operator's config file wins over a recorded choice; the result says "
            "`in_effect: false` when it does."
        ),
        "properties": {
            "knob": ("string", "The choice, e.g. port_strategy (see ddflow_flow_show).", True),
            "value": ("string", "One of its options.", True),
            "reason": ("string", "Why this suits the project.", False),
        },
        "api": lambda repo, a, agent: _api().flow_choose(
            repo, a["knob"], a["value"], reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": "",
    },
}
