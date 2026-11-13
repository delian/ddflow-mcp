"""The gate pipeline's commands: approve, and gate status/list/run/verify/record/skip."""

from __future__ import annotations

from ._shared import (
    _APPROVE_EPILOG,
    _OVERRIDE,
    GATE_OUTCOMES,
    OK,
    OVERRIDE_EXEMPT,
    CliPolicy,
    Command,
    L,
    Param,
    _api,
    argparse,
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("approve",),
        reason=(
            "clears a HUMAN-approval gate, and the whole point is that the agent cannot. "
            "A human checkpoint reachable from the MCP surface is not a human checkpoint "
            "\u2014 it is a second `gate record` with a longer name. This exemption is the "
            "feature, not an oversight, and `test_no_mcp_tool_can_clear_a_human_gate` "
            "asserts it holds end to end rather than resting on this line."
        ),
        summary="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_APPROVE_EPILOG,
        params=(
            Param("id", positional=True),
            Param("gate", positional=True),
            Param("note", help="what you looked at, for the record"),
            Param("reject", type="boolean", help="refuse it; --reason required"),
            Param("reason", help="why it was rejected — a 'no' nobody can act on is a stall"),
            Param(
                "approval",
                help="an agent: the id of the operator's approval of exactly this acceptance (a refusal files the request and names it)",
            ),
        ),
    ),
    Command(
        path=("gate", "status"),
        tool="ddflow_gate_status",
        description="Where an item stands in its quality pipeline, which gate is next, and the "
        "instruction for that gate. Gates marked '?' did not run — that is a coverage "
        "gap, never a pass.",
        kind="gate.status",
        prose=True,
        prose_reason="carries the next gate's INSTRUCTION, which is the useful half",
        defaults={"gate": ""},
        call=lambda repo, a, agent: _api().gate_status(repo, a["id"], agent=agent),
        # PROSE: the body carries the next gate's INSTRUCTION, which is the half an
        # agent acts on. `--json` gives the structured pipeline instead.
        payload="text",
        render=L.gate_text,
        cli=CliPolicy(shown=L.gate_listed, body=lambda out, a: out.body("status")),
        params=(Param("id", help="Item id.", cli_help="", positional=True),),
    ),
    Command(
        path=("gate", "list"),
        summary="the gates this project defines; --refuted: passed on refutation; --overrides: set aside",
        tool="ddflow_gate_list",
        description="The gates this project defines; with refuted=true, every gate passed ON "
        "REFUTATION, to spot-check.",
        kind="gate.list",
        prose=True,
        prose_reason="one row per gate (or per gate passed on refutation), meant to be read as lines",
        call=lambda repo, a, agent: _api().gate_list(
            repo,
            refuted=bool(a.get("refuted")),
            since=str(a.get("since") or ""),
            agent=agent,
            overrides=bool(a.get("overrides")),
        ),
        payload="text",
        render=L.gate_text,
        cli=CliPolicy(
            shown=L.gate_listed,
            body=lambda out, a: out.body(("refuted", "count", "passes", "gates", "overrides")),
        ),
        params=(
            Param(
                "refuted",
                type="boolean",
                help="List the gates passed on refutation instead.",
                cli_help="list every gate recorded passed ON REFUTATION (D-unify 5), to spot-check",
            ),
            Param(
                "overrides",
                type="boolean",
                help="List the overrides instead: forced completions, skips, claim forces.",
                cli_help="list every override on the record (forced completion, gate skip, claim "
                "force, typed-chat T3 use, relayed consent) with who approved it at which level",
            ),
            Param(
                "since",
                help="With refuted: since this ISO date.",
                cli_help="with --refuted: only passes recorded at or after this ISO date or timestamp",
                default="",
            ),
        ),
    ),
    Command(
        path=("gate", "run"),
        tool="ddflow_gate_run",
        description="Execute a command gate (tests, linters) and record the result "
        "with its evidence. Agent gates cannot be run this way; they are "
        "recorded with ddflow_gate_record.",
        call=lambda repo, a, agent, called_from=None: _api().gate_run(
            repo,
            a["id"],
            a["gate"],
            agent=agent,
            called_from=called_from,
            rerun_failed=bool(a.get("rerun_failed")),
            force=bool(a.get("force")),
            reason=a.get("reason", "") or "",
            timeout_s=a.get("timeout"),
        ),
        payload=("gate", "outcome", "evidence"),
        render=L.run_text,
        cli=CliPolicy(shown=L.run_shown),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id, e.g. unit_tests.", cli_help="", positional=True),
            Param("reason", cli_only=True, cli_help="why --force is needed (recorded)", default=""),
            Param(
                "force",
                type="boolean",
                cli_only=True,
                cli_help="override ANOTHER CLONE's live lease on the item (needs --reason; recorded on the event)",
            ),
            Param(
                "timeout",
                type="integer",
                help="Seconds this run may take (at most [gates].max_run_timeout_s).",
                cli_help="seconds THIS run may take instead of the gate's own timeout (and, for ci, [ci].timeout_s); at most [gates].max_run_timeout_s",
            ),
            Param(
                "rerun_failed",
                type="boolean",
                cli_only=True,
                cli_help="when the gate fails, run ONLY the failed tests once more and record both outcomes (the gate stays failed; a test that passes alone goes to the flake log)",
            ),
        ),
        flag_exempt={
            **OVERRIDE_EXEMPT,
            "--rerun-failed": "a terminal-side diagnosis of a flaky failure; the gate stays FAILED either way, so an agent gains nothing from it over the evidence's failed_tests (tools/list byte budget)",
        },
    ),
    Command(
        path=("gate", "verify"),
        summary="break what this gate guards and require it to notice (exit 1 = it cannot)",
        tool="ddflow_gate_verify",
        description="Break what a gate guards and require it to NOTICE: applies each mutation "
        "registered on the gate, runs it, requires a non-zero exit, restores the "
        "file. A gate that cannot fail reports success on every change. Exit 1: "
        "the gate did NOT catch its mutation, or none is registered. Exit 3 on a "
        "HUMAN-approval gate (nothing to mutate). A mutation whose `old` text is "
        "absent or ambiguous is a FAILURE, not a skip: the gate ran on pristine "
        "source.",
        call=lambda repo, a, agent: _api().gate_verify(repo, a["id"], a["gate"], agent=agent),
        payload=("gate", "reason", "results", "verified"),
        render=L.verify_text,
        cli=CliPolicy(notes=L.verify_notes),
        params=(
            Param("id", help="Item whose worktree to mutate in.", cli_help="", positional=True),
            Param("gate", help="Gate id. Must be a command gate.", cli_help="", positional=True),
        ),
    ),
    Command(
        path=("gate", "record"),
        tool="ddflow_gate_record",
        flag_exempt={"--force": _OVERRIDE},
        description="Record the outcome of a gate you performed (research, a review, a bug hunt). "
        "outcome is one of passed/failed/unavailable/partial/skipped. "
        "If a reviewer or tool could not run, record 'unavailable' with a reason, "
        "never 'passed'. Pass the reviewer's model for the family check.",
        call=lambda repo, a, agent, called_from=None: _api().gate_record(
            repo,
            a["id"],
            a["gate"],
            outcome=a.get("outcome", "passed") or "passed",
            reason=a.get("reason", "") or "",
            evidence=_api().GateEvidence(
                note=a.get("evidence", "") or "",
                command=a.get("command", "") or "",
                exit_code=a.get("exit_code"),
                model=a.get("reviewer_model", "") or a.get("model", "") or "",
                model_is_reviewer=bool(a.get("reviewer_model")),
                reviewed_sha=a.get("reviewed_sha", "") or "",
                output_file=a.get("output_file", "") or "",
            ),
            agent=agent,
            called_from=called_from,
            force=bool(a.get("force")),
            approval=a.get("approval", "") or "",
        ),
        payload=("gate", "outcome", "warning"),
        render=L.record_text,
        cli=CliPolicy(
            shown=(OK,),
            notes=L.record_notes,
            body=lambda out, a: out.body(("gate", "outcome")),
        ),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id.", cli_help="", positional=True),
            Param(
                "outcome",
                help="passed | failed | unavailable | partial | skipped",
                cli_help="failed, unavailable, partial and skipped each require --reason",
                tool_required=True,
                default="passed",
                choices=tuple(GATE_OUTCOMES),
            ),
            Param(
                "reason",
                help="Why the gate did not pass. Required for failed/unavailable/partial/skipped; --evidence is what you observed, not a substitute.",
                cli_help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, partial and skipped (--evidence is what you observed, not a substitute)",
                default="",
            ),
            Param(
                "evidence",
                help="What you ran and what it said: the output you observed (a summary).",
                cli_help="what you observed: output, a summary",
                default="",
            ),
            Param(
                "command",
                help="The command you ran. With `exit_code` it makes an outcome evidence; a gate in `gates.evidence_required` is rejected without them.",
                cli_help="",
                default="",
            ),
            Param("exit_code", help="That command's exit code.", cli_help="", cli_type=int),
            Param(
                "model",
                help="REVIEWER's model, e.g. 'gemini-2.5-pro'.",
                cli_help="the REVIEWER's model, for family independence (the author's is `complete --model`); an author-family name on a reviewer gate is refused",
                default="",
            ),
            Param(
                "reviewer_model",
                help="Like `model`; says it IS the reviewer.",
                cli_help="like --model, stating that this model IS the reviewer, so an author-family name is recorded rather than refused",
                default="",
            ),
            Param(
                "reviewed_sha",
                help="Commit reviewed (roborev review <sha>); must be the branch.",
                cli_help="the commit the review tool ran on (roborev review <sha>); refused when it is not the item's branch head or a commit of its branch",
                default="",
            ),
            Param(
                "output_file",
                help="Path to its full output; a digest is recorded.",
                cli_help="",
                default="",
            ),
            Param(
                "approval",
                help="An operator approval id (Q...) for this exact override; a refusal names it.",
                cli_help="the id of the operator's approval of this exact override: a refused call files the request and names the id; a person answers it with `ddflow approvals approve`",
                default="",
            ),
            Param(
                "force",
                type="boolean",
                cli_only=True,
                cli_help="override ANOTHER CLONE's live lease on the item (needs --reason; recorded on the event)",
            ),
        ),
        tool_order=(
            "id",
            "gate",
            "outcome",
            "reason",
            "evidence",
            "model",
            "reviewer_model",
            "reviewed_sha",
            "command",
            "exit_code",
            "output_file",
            "approval",
        ),
    ),
    Command(
        path=("gate", "skip"),
        tool="ddflow_gate_skip",
        flag_exempt={
            "--force": _OVERRIDE,
            "--outcome": "a skip IS the outcome",
            "--evidence": "a skipped gate produced none; that is what skipped means",
            "--command": "nothing ran",
            "--exit-code": "nothing ran",
            "--output-file": "nothing ran",
            "--model": "no reviewer performed it",
        },
        description="Skip a gate ON THE RECORD, with a mandatory reason: the auditable escape hatch. `gates.require_outcome` means a silent gate BLOCKS completion, so the alternative to a skip is forcing past everything at once; a skip names the single step dropped and why, permanently in the log. A gate in `gates.required` still blocks when skipped.",
        call=lambda repo, a, agent: _api().gate_record(
            repo,
            a["id"],
            a["gate"],
            skip=True,
            reason=a.get("reason", "") or "",
            evidence=_api().GateEvidence(
                note=a.get("evidence", "") or "",
                command=a.get("command", "") or "",
                exit_code=a.get("exit_code"),
                model=a.get("model", "") or "",
                output_file=a.get("output_file", "") or "",
            ),
            agent=agent,
            force=bool(a.get("force")),
            approval=a.get("approval", "") or "",
        ),
        payload=("gate", "outcome"),
        render=L.record_text,
        cli=CliPolicy(shown=(OK,), notes=L.record_notes),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id.", cli_help="", positional=True),
            Param(
                "outcome",
                help="failed, unavailable, partial and skipped each require --reason",
                default="passed",
                choices=tuple(GATE_OUTCOMES),
                cli_only=True,
            ),
            Param(
                "reason",
                help="Why this step does not apply HERE. 'n/a' is not a reason: the next person reads this to decide whether you were right.",
                cli_help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, partial and skipped (--evidence is what you observed, not a substitute)",
                tool_required=True,
                default="",
            ),
            Param(
                "evidence", help="what you observed: output, a summary", default="", cli_only=True
            ),
            Param("command", default="", cli_only=True),
            Param("exit_code", type="integer", cli_only=True),
            Param(
                "model",
                help="the REVIEWER's model, for family independence (the author's is `complete --model`); an author-family name on a reviewer gate is refused",
                default="",
                cli_only=True,
            ),
            Param("output_file", default="", cli_only=True),
            Param(
                "approval",
                help="An operator approval id (Q...) for this exact override; a refusal names it.",
                cli_help="the id of the operator's approval of this exact override: a refused call files the request and names the id; a person answers it with `ddflow approvals approve`",
                default="",
            ),
            Param(
                "force",
                type="boolean",
                cli_only=True,
                cli_help="override ANOTHER CLONE's live lease on the item (needs --reason; recorded on the event)",
            ),
        ),
    ),
)
