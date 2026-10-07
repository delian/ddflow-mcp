"""MCP tools: setup, configuration, reviewers and reviews.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

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
    "ddflow_reviewers_detect": {
        "description": (
            "Probe well-known local ports for an OpenAI-compatible model server (ollama, vLLM, LM Studio, llama.cpp, sglang) and report what serves, with each model's pretraining family: how to find a reviewer from a DIFFERENT family than yourself, which the critic gate requires. write=true records it in the git-ignored .ddflow/local/reviewers.toml (this machine's, never committed)."
        ),
        "properties": {
            "write": (
                "boolean",
                "Append the discovered reviewers to .ddflow/local/reviewers.toml.",
                False,
            ),
            "shared": (
                "boolean",
                "With write: commit them to .ddflow/config.toml instead, for every "
                "clone. Only for a reviewer the whole team reaches at the same address.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().reviewers_detect(
            repo, write=bool(a.get("write")), shared=bool(a.get("shared")), agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "reviewers.detect",
    },
    "ddflow_reviewers_list": {
        "description": "Show the configured reviewers, their families and which gates they serve.",
        "properties": {},
        "api": lambda repo, a, agent: _api().reviewers_list(repo, agent=agent),
        "payload": "text",
        "text": True,
        "kind": "reviewers.list",
    },
    "ddflow_review": {
        "description": (
            "Run the configured cross-family reviewer over an item's diff and record the "
            "result: the critic gate, performed by ddflow. "
            "No reviewer, endpoint or verdict records UNAVAILABLE, never a pass. A gate gets [review].max_rounds (default 2) rounds, delta too, "
            "then ddflow_review_triage."
        ),
        "properties": {
            "id": ("string", "Item whose diff to review.", True),
            "gate": (
                "string",
                "critic|rubber_duck|rubber_duck,critic",
                False,
            ),
            "intent": (
                "string",
                "What the change is MEANT to do. The reviewer flags where the diff "
                "and the intent disagree, so without it there is nothing to "
                "disagree with. Defaults to the item's title and body.",
                False,
            ),
            "base": ("string", "Ref to diff against (default: the base branch).", False),
            "context": ("string", "Extra context for the reviewer.", False),
            "commit": (
                "string",
                "Review this ONE landed commit (against its first parent) instead of the "
                "item's branch: the after-merge review, when the branch is gone.",
                False,
            ),
            "branch": (
                "string",
                "Review this branch against base, for an item claimed with no_worktree (default: the branch checked out where this connection runs). With neither, the review is recorded unavailable.",
                False,
            ),
            "delta": (
                "boolean",
                "Recheck only what changed since the reviewed head.",
                False,
            ),
            "full": (
                "boolean",
                "Force a full round (when delta_default is on).",
                False,
            ),
            "chunk": (
                "array",
                "Re-review ONLY these chunk numbers (as the recorded review numbered them, "
                "e.g. [5]) and merge into that record; needs the same diff, chunk size and reviewer.",
                False,
            ),
        },
        "api": lambda repo, a, agent, called_from=None, on_progress=None: _api().run_review(
            repo,
            on_progress=on_progress,
            gate=a.get("gate") or "critic",
            item=a.get("id", "") or "",
            intent=a.get("intent", "") or "",
            context=a.get("context", "") or "",
            base=a.get("base", "") or "",
            commit=a.get("commit", "") or "",
            branch=a.get("branch", "") or "",
            called_from=called_from,
            agent=agent,
            chunks=a.get("chunk") or None,
            delta=bool(a.get("delta")),
            full=bool(a.get("full")),
        ),
        "wants_called_from": True,
        "wants_progress": True,
        # The TRANSCRIPT the run produced — findings already formatted with their
        # severities, which is what this tool has always returned.
        "payload": "text",
        "text": True,
        "kind": "review",
    },
    "ddflow_review_triage": {
        "description": (
            "Record your triage of ONE finding of an item's recorded `ddflow review`: it is "
            "refuted (probe = the run that shows it false) or confirmed (probe = the fix or "
            "test that answers it). The finding number is the #N the review printed. The "
            "gate stays failed (D-review-triage) until, after the round cap, the triage of "
            "its last finding records it passed on refutation, flagged."
        ),
        "properties": {
            "id": ("string", "The item whose review it is.", True),
            "gate": ("string", "Omit if one gate has findings.", False),
            "finding": (
                "integer",
                "The finding's number: #N in the review's output -- of the RECORDED "
                "reviewer's findings, which the output's last lines name when several ran.",
                True,
            ),
            "verdict": ("string", "refuted or confirmed.", True),
            "probe": ("string", "The evidence for the verdict. Required.", True),
        },
        "api": lambda repo, a, agent: _api().review_triage(
            repo,
            a.get("id", "") or "",
            gate=a.get("gate") or "",
            finding=int(a.get("finding") or 0),
            verdict=a.get("verdict", "") or "",
            probe=a.get("probe", "") or "",
            agent=agent,
        ),
        "payload": "text",
        "text": True,
        "kind": "review.triage",
    },
}
