"""The review commands, declared once: reviewers (detect, list, presets, add, approve, test),
review (with `review triage`'s tool) and verify.

`surfaces/parsers/review.py` registers their command-line half and `surfaces/tools/` takes
their MCP entries (D-unify 4, B-uni-cmd-migrate.6-rest). `ddflow review` is one parser for two
commands (the run, and `review triage <id> ...`); `ddflow_review_triage` is the tool of the second.
"""

from __future__ import annotations

import argparse

from ..registry import Command, Param, by_tool
from ..tools._common import _api

_REVIEW_EPILOG = (
    "two commands share this parser:\n"
    "  ddflow review <id> --gate G [--chunk N | --delta]   run the reviewer(s) (slow, shared);\n"
    "                  a gate gets [review].max_rounds (default 2) rounds, full or delta, then triage\n"
    "  ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ...\n"
    "                                            record what became of one finding\n"
    "--finding/--refuted/--confirmed/--probe belong to the second form only. It REQUIRES the\n"
    "`triage` verb: without it they are refused, never run as a review."
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("reviewers", "detect"),
        summary="probe well-known local endpoints",
        tool="ddflow_reviewers_detect",
        prose_reason="a probe report naming each endpoint and what answered",
        description="Probe well-known local ports for an OpenAI-compatible model server (ollama, vLLM, LM Studio, llama.cpp, sglang) and report what serves, with each model's pretraining family: how to find a reviewer from a DIFFERENT family than yourself, which the critic gate requires. write=true records it in the git-ignored .ddflow/local/reviewers.toml (this machine's, never committed).",
        kind="reviewers.detect",
        prose=True,
        call=lambda repo, a, agent: _api().reviewers_detect(
            repo, write=bool(a.get("write")), shared=bool(a.get("shared")), agent=agent
        ),
        payload="text",
        params=(
            Param(
                "write",
                type="boolean",
                help="Append the discovered reviewers to .ddflow/local/reviewers.toml.",
                cli_help="append the discovered reviewers to the git-ignored .ddflow/local/reviewers.toml",
            ),
            Param(
                "shared",
                type="boolean",
                help="With write: commit them to .ddflow/config.toml instead, for every clone. Only for a reviewer the whole team reaches at the same address.",
                cli_help="with --write: commit them to .ddflow/config.toml instead, for every clone",
            ),
        ),
    ),
    Command(
        path=("reviewers", "list"),
        tool="ddflow_reviewers_list",
        prose_reason="a table, plus the warning about unclassified reviewers",
        description="Show the configured reviewers, their families and which gates they serve.",
        kind="reviewers.list",
        prose=True,
        call=lambda repo, a, agent: _api().reviewers_list(repo, agent=agent),
        payload="text",
        params=(),
    ),
    Command(
        path=("reviewers", "presets"),
        reason="lists boilerplate for authoring reviewer config, which pairs with `reviewers add` — an operator edit, exempt for the same reason",
        summary="ready-made provider settings",
        params=(),
    ),
    Command(
        path=("reviewers", "add"),
        reason="writes an API-key env-var name into project config; a config edit an operator should make deliberately, not an agent mid-task",
        summary="add a reviewer from a preset",
        params=(
            Param("preset", help="see `ddflow reviewers presets`", default=""),
            Param("name", default=""),
            Param("model", default=""),
            Param("base_url", default=""),
            Param("gates", default=""),
            Param(
                "shared",
                type="boolean",
                help="write to the committed .ddflow/config.toml instead of the git-ignored .ddflow/local/reviewers.toml -- only for a reviewer every clone should use",
            ),
            Param(
                "no_launch",
                type="boolean",
                help="do not auto-start a local server for this reviewer",
            ),
        ),
    ),
    Command(
        path=("reviewers", "approve"),
        reason="a PERSON vouches for a tool-written reviewer (decision D-reviewer-trust); an agent that could approve the reviewer it wrote would make the record decorative. test_approve_is_not_an_mcp_tool asserts there is no such tool.",
        summary="a PERSON vouches for a tool-written reviewer entry (refused under an agent identity); with no name, list the entries waiting (anyone may)",
        params=(
            Param("name", positional=True, nargs="?", default=""),
            Param("note", default=""),
        ),
    ),
    Command(
        path=("reviewers", "test"),
        reason="covered by ddflow_reviewers_detect, which probes the same way",
        summary="send a tiny known-buggy diff and check the reply",
        params=(Param("name", positional=True, nargs="?", default=""),),
    ),
    Command(
        path=("review",),
        summary="run the configured reviewer(s) over an item's diff",
        tool="ddflow_review",
        prose_reason="reviewer findings, already formatted with their severities",
        flag_exempt={
            "--finding": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--refuted": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--confirmed": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--probe": "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)",
            "--force": "lifting the review-round budget belongs to the operator",
            "--reason": "lifting the review-round budget belongs to the operator",
        },
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_REVIEW_EPILOG,
        wants_progress=True,
        description="Run the configured cross-family reviewer over an item's diff and record the "
        "result: the critic gate, performed by ddflow. "
        "No reviewer, endpoint or verdict records UNAVAILABLE, never a pass. A gate gets [review].max_rounds (default 2) rounds, delta too, "
        "then ddflow_review_triage.",
        kind="review",
        prose=True,
        call=lambda repo, a, agent, called_from=None, on_progress=None: _api().run_review(
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
        # The TRANSCRIPT the run produced — findings already formatted with their
        # severities, which is what this tool has always returned.
        payload="text",
        wants_called_from=True,
        params=(
            Param(
                "id",
                help="Item whose diff to review.",
                cli_help="the item; or `triage <item>`",
                tool_required=True,
                positional=True,
                nargs="*",
                default=[],
            ),
            Param(
                "gate",
                help="critic|rubber_duck|rubber_duck,critic",
                cli_help="gate to review (default critic); rubber_duck,critic reviews both -- one combined review under [review].combined_under_lines changed lines",
            ),
            Param(
                "intent",
                help="What the change is MEANT to do. The reviewer flags where the diff and the intent disagree, so without it there is nothing to disagree with. Defaults to the item's title and body.",
                cli_help="what the change is meant to do (defaults to the item's title/body)",
                default="",
            ),
            Param("context", help="Extra context for the reviewer.", cli_help="", default=""),
            Param(
                "base",
                help="Ref to diff against (default: the base branch).",
                cli_help="",
                default="",
            ),
            Param(
                "commit",
                help="Review this ONE landed commit (against its first parent) instead of the item's branch: the after-merge review, when the branch is gone.",
                cli_help="review this one landed commit (vs its first parent) instead of the item's branch",
                default="",
            ),
            Param(
                "branch",
                help="Review this branch against base, for an item claimed with no_worktree (default: the branch checked out where this connection runs). With neither, the review is recorded unavailable.",
                cli_help="review this branch against base -- for an item claimed without a worktree (default: the branch checked out in the worktree you are standing in)",
                default="",
            ),
            Param(
                "chunk",
                type="array",
                help="Re-review ONLY these chunk numbers (as the recorded review numbered them, e.g. [5]) and merge into that record; needs the same diff, chunk size and reviewer.",
                cli_help="re-review only chunk N (as the recorded review numbered it; repeatable, or '2,5') and merge it into that record -- same diff, chunk size and reviewer",
                action="append",
                default=[],
            ),
            Param(
                "delta",
                type="boolean",
                help="Recheck only what changed since the reviewed head.",
                cli_help="recheck ONLY what changed since the head the gate's last review covered, for a diff too large to send twice: not a full round, but counted against [review].max_rounds. A plain re-review is a full round with the previous findings; [review].delta_default = true makes it a delta instead",
            ),
            Param(
                "full",
                type="boolean",
                help="Force a full round (when delta_default is on).",
                cli_help="review the item's WHOLE diff even when [review].delta_default would make it a delta: a full round, counted against [review].max_rounds",
            ),
            Param(
                "force",
                type="boolean",
                help="run a full round past [review].max_rounds; needs --reason, recorded in the evidence",
                cli_only=True,
            ),
            Param("reason", help="why --force", default="", cli_only=True),
            Param(
                "finding", type="integer", help="triage: the finding's number (#N)", cli_only=True
            ),
            Param(
                "refuted",
                type="boolean",
                help="triage: --probe shows the finding is false",
                cli_only=True,
            ),
            Param(
                "confirmed",
                type="boolean",
                help="triage: --probe is the fix/test that answers it",
                cli_only=True,
            ),
            Param("probe", help="triage: the evidence for the verdict", cli_only=True),
        ),
        tool_order=(
            "id",
            "gate",
            "intent",
            "base",
            "context",
            "commit",
            "branch",
            "delta",
            "full",
            "chunk",
        ),
    ),
    Command(
        path=(),
        tool="ddflow_review_triage",
        prose_reason="one confirmation line, which is the whole answer",
        description="Record your triage of ONE finding of an item's recorded `ddflow review`: it is "
        "refuted (probe = the run that shows it false) or confirmed (probe = the fix or "
        "test that answers it). The finding number is the #N the review printed. The "
        "gate stays failed (D-review-triage) until, after the round cap, the triage of "
        "its last finding records it passed on refutation, flagged.",
        kind="review.triage",
        prose=True,
        call=lambda repo, a, agent: _api().review_triage(
            repo,
            a.get("id", "") or "",
            gate=a.get("gate") or "",
            finding=int(a.get("finding") or 0),
            verdict=a.get("verdict", "") or "",
            probe=a.get("probe", "") or "",
            agent=agent,
        ),
        payload="text",
        params=(
            Param("id", help="The item whose review it is.", required=True, mcp_only=True),
            Param("gate", help="Omit if one gate has findings.", mcp_only=True),
            Param(
                "finding",
                type="integer",
                help="The finding's number: #N in the review's output -- of the RECORDED reviewer's findings, which the output's last lines name when several ran.",
                required=True,
                mcp_only=True,
            ),
            Param("verdict", help="refuted or confirmed.", required=True, mcp_only=True),
            Param(
                "probe",
                help="The evidence for the verdict. Required.",
                required=True,
                mcp_only=True,
            ),
        ),
    ),
    Command(
        path=("verify",),
        summary="re-derive the claims behind a done task, or sweep them all (exit 1 = one fails)",
        tool="ddflow_verify",
        flag_exempt={"--all": "a sweep is what omitting `id` means"},
        description="Re-check a done task's claims; fails if one does not hold. No id: sweep all, worst first.",
        call=lambda repo, a, agent: _api().verify_tool(
            repo,
            id=a.get("id", "") or "",
            phase=a.get("phase", "") or "",
            limit=int(a["limit"]) if a.get("limit") is not None else None,
            file_bugs=bool(a.get("file_bugs")),
            reopen=bool(a.get("reopen")),
            reason=a.get("reason", "") or "",
            force=bool(a.get("force")),
            pack_=bool(a.get("pack")),
            judge_=bool(a.get("judge")),
            agent=agent,
        ),
        payload="",
        params=(
            Param(
                "id",
                help="Task id; omit to sweep.",
                cli_help="one done task; omit with --all/--phase",
                positional=True,
                nargs="?",
                default="",
            ),
            Param("all", type="boolean", help="every done task, worst first", cli_only=True),
            Param(
                "phase",
                help="Sweep only this phase.",
                cli_help="every done task under this phase",
                default="",
            ),
            Param(
                "limit",
                type="integer",
                help="How many of the worst to list (20).",
                cli_help="how many of the worst to list (20)",
            ),
            Param(
                "file_bugs",
                type="boolean",
                help="File a bug per failing completion.",
                cli_help="file a bug for each completion that does not hold",
            ),
            Param(
                "reopen",
                type="boolean",
                help="Reopen a failing completion.",
                cli_help="send a task whose completion fails back to the queue",
            ),
            Param(
                "reason",
                help="Why (reopen).",
                cli_help="with --reopen: why (default: the failed claims)",
                default="",
            ),
            Param(
                "force",
                type="boolean",
                help="Reopen even if it holds.",
                cli_help="with --reopen: even when the completion holds",
            ),
            Param(
                "pack",
                type="boolean",
                help="Evidence pack for a verifier.",
                cli_help="print the evidence pack for an independent verifier",
            ),
            Param(
                "judge",
                type="boolean",
                help="Cross-family reviewer judges it.",
                cli_help="have the configured cross-family reviewer judge it (gate: verify)",
            ),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
