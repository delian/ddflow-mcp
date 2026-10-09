"""The record-keeping commands, declared once: research, bugs and sessions.

`surfaces/parsers/records.py` registers their command-line half and `surfaces/tools/` takes
their MCP entries (D-unify 4, B-uni-cmd-migrate.4-adapters). `bug reopen` has no tool of its
own: `ddflow_bug_invalid` with `reopen` serves it.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _answer, _api, _bug_reopen, _regression_tests, _reopening
from .answer import ANSWER_FLAG_EXEMPT, ANSWER_PARAMS

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("research",),
        tool="ddflow_research_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Record a research finding. verdict MUST be CONFIRMED, REFUTED or "
        "THEORETICAL, and CONFIRMED/REFUTED require a probe — a verdict with no "
        "probe behind it is an opinion. A REFUTED entry is as valuable as an "
        "adopted one: it stops the next session re-researching it.",
        call=lambda repo, a, agent: _api().research_add(
            repo,
            _api().ResearchFinding(
                question=a.get("question", "") or "",
                verdict=a.get("verdict", "THEORETICAL") or "THEORETICAL",
                claim=a.get("claim", "") or "",
                mechanism=a.get("mechanism", "") or "",
                falsifier=a.get("falsifier", "") or "",
                probe=a.get("probe", "") or "",
                probe_output=a.get("probe_output", "") or "",
                sources=a.get("sources", "") or "",
                budget=a.get("budget", "") or "",
                item=a.get("item", "") or "",
                id=a.get("id", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        payload=("id", "verdict"),
        params=(
            Param(
                "verb",
                help="optional: `research add` = `research`",
                positional=True,
                nargs="?",
                choices=("add",),
                cli_only=True,
            ),
            Param(
                "id",
                help="Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                cli_help="",
                default="",
            ),
            Param("question", help="What was asked.", cli_help="", required=True),
            Param("claim", help="The falsifiable claim.", cli_help="", default=""),
            Param(
                "mechanism",
                help="WHY it would work in this repo. The middle field of the triple — claim, mechanism, falsifier — and the one most often skipped.",
                cli_help="",
                default="",
            ),
            Param(
                "falsifier",
                help="The single observation that would kill it.",
                cli_help="",
                default="",
            ),
            Param("probe", help="The command you ran.", cli_help="", default=""),
            Param("probe_output", help="Its output, verbatim.", cli_help="", default=""),
            Param("verdict", help="CONFIRMED | REFUTED | THEORETICAL", cli_help="", required=True),
            Param(
                "sources",
                help="Comma-separated URLs/DOIs you actually opened.",
                cli_help="",
                default="",
            ),
            Param(
                "budget",
                help="What you allowed yourself, e.g. '30 min, no GPU'.",
                cli_help="",
                default="",
            ),
            Param("item", help="The task this research is for.", cli_help="", default=""),
            *ANSWER_PARAMS,
        ),
        tool_order=(
            "id",
            "question",
            "verdict",
            "claim",
            "falsifier",
            "probe",
            "probe_output",
            "sources",
            "mechanism",
            "budget",
            "item",
            "relation",
            "check_only",
        ),
    ),
    Command(
        path=("bug", "found"),
        tool="ddflow_bug_found",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Report a bug the moment you find it, BEFORE fixing it. Recording it first "
        "makes the fix accountable: `ddflow_bug_fixed` refuses to close one without "
        "a regression test. A hunt that records nothing looks like one that found nothing.",
        call=lambda repo, a, agent: _api().bug_found(
            repo,
            summary=a.get("summary", "") or "",
            item=a.get("item", "") or "",
            id=a.get("id", "") or "",
            title=a.get("title", "") or "",
            severity=a.get("severity", "") or "",
            scope=a.get("scope", "") or "",
            globs=a.get("globs", "") or "",
            no_task=bool(a.get("no_task")),
            answer=_answer(a),
            agent=agent,
        ),
        payload=("id", "offer", "fix_task", "fix_task_filed"),
        params=(
            Param(
                "id",
                help="Stable id, e.g. 'B1'. You will cite it when closing.",
                cli_help="",
                default="",
            ),
            Param("summary", help="What is wrong, in one line.", cli_help="", required=True),
            Param("item", help="The task it was found in or affects.", cli_help="", default=""),
            Param(
                "title", help="Short headline.", cli_help="a short headline for the bug", default=""
            ),
            Param(
                "severity",
                help="low|medium|high|critical.",
                cli_help="low | medium | high | critical (optional)",
                default="",
            ),
            Param(
                "scope",
                help="project (default) or ddflow.",
                cli_help="project (default), or ddflow for a bug in ddflow itself",
                default="",
            ),
            Param(
                "globs",
                help="The fix task's files; default: the item's globs.",
                cli_help="the fix task's files (comma-separated); default: the item's own globs",
                default="",
            ),
            Param(
                "no_task",
                type="boolean",
                help="File no fix task (fixed in the same commit).",
                cli_help="file no fix task: the bug is fixed in the commit that found it",
            ),
            *ANSWER_PARAMS,
        ),
    ),
    Command(
        path=("bug", "file-tasks"),
        summary="file a fix task for every open bug that has none (one-shot, after an upgrade)",
        tool="ddflow_bug_file_tasks",
        description="File a fix task for every open bug that has none (one-shot after an upgrade; "
        "`ddflow_bug_found` files one per bug by default).",
        call=lambda repo, a, agent: _api().bug_file_tasks(
            repo, dry_run=bool(a.get("dry_run")), agent=agent
        ),
        payload=("filed", "linked", "tasks", "links", "dry_run"),
        params=(
            Param(
                "dry_run",
                type="boolean",
                help="List what would be filed; write nothing.",
                cli_help="list what would be filed",
            ),
        ),
    ),
    Command(
        path=("bug", "fixed"),
        tool="ddflow_bug_fixed",
        description="Close a bug. Requires the name of the regression test that would "
        "catch it again — write the test, watch it FAIL against the "
        "unfixed code, then close.",
        call=lambda repo, a, agent: _api().bug_fixed(
            repo,
            a["id"],
            regression_test=_regression_tests(a),
            lesson=a.get("lesson", "") or "",
            lesson_title=a.get("lesson_title", "") or "",
            lesson_rule=a.get("lesson_rule", "") or "",
            changelog=a.get("changelog", "") or "",
            verify_regression=not a.get("skip_regression_verify", False),
            verify_reason=a.get("verify_reason", "") or "",
            agent=agent,
        ),
        payload=("id", "regression_verified", "lesson_captured", "lesson_capture"),
        params=(
            Param("id", help="Bug id.", cli_help="", positional=True),
            Param(
                "regression_test",
                help="Test that now guards this. Several: separate them with ',' or ';', or pass regression_tests.",
                cli_help="the test that now guards this bug; repeat it, or separate with ',' or ';', for several",
                action="append",
                default=[],
            ),
            Param(
                "skip_regression_verify",
                type="boolean",
                help="Do NOT run the named test on the pre-fix tree (default false: it is run and must FAIL there, PASS with the fix). Needs verify_reason.",
                cli_help="do not run the named test on the pre-fix tree; requires --verify-reason (recorded)",
            ),
            Param(
                "verify_reason",
                help="Why the pre-fix regression check is skipped (recorded). Required when skip_regression_verify is true.",
                cli_help="why the pre-fix regression check is skipped (recorded)",
                default="",
            ),
            Param(
                "lesson",
                help="Id of an EXISTING lesson this bug belongs to.",
                cli_help="",
                default="",
            ),
            Param(
                "lesson_title", help="Capture a lesson at the same time.", cli_help="", default=""
            ),
            Param(
                "lesson_rule",
                help="The lesson in full — the transferable rule, not the incident. A future agent on a different task has to be able to apply it.",
                cli_help="",
                default="",
            ),
            Param(
                "changelog",
                help="Optional 'Fixed: text' (any category), or skip.",
                cli_help="'Fixed: text' (any category), or skip / internal",
                default="",
            ),
            Param(
                "regression_tests",
                type="array",
                help="The tests that now guard this, one per element -- the list form of regression_test. One of the two is required.",
                mcp_only=True,
            ),
        ),
        tool_order=(
            "id",
            "regression_test",
            "regression_tests",
            "lesson_title",
            "lesson_rule",
            "lesson",
            "changelog",
            "skip_regression_verify",
            "verify_reason",
        ),
    ),
    Command(
        path=("bug", "invalid"),
        summary="close a bug as a FALSE finding (never as fixed), with why and the probe",
        tool="ddflow_bug_invalid",
        description="Close a bug as a FALSE finding -- nothing was broken, so nothing was fixed. "
        "Never counts as a fix. Requires the reason; give the probe or test that "
        "showed it false as evidence. Refused (exit 3) for an unknown id or a bug "
        "already closed; a real bug is closed with `ddflow_bug_fixed` instead. "
        "`reopen`: instead UNDO a closure (fixed or invalid) made by mistake.",
        call=lambda repo, a, agent: (
            _bug_reopen(repo, a, agent=agent)
            if _reopening(a)
            else _api().bug_invalid(
                repo,
                a["id"],
                reason=a.get("reason", "") or "",
                evidence=a.get("evidence", "") or "",
                agent=agent,
            )
        ),
        payload=lambda a: (
            ("id", "was", "reason_given", "fix_task", "fix_task_state", "previous_fix_task", "next")
            if _reopening(a)
            else (
                "id",
                "invalid_reason",
                "evidence",
                "unchecked",
                "fix_task",
                "fix_task_removed",
                "fix_task_kept",
            )
        ),
        params=(
            Param("id", help="Bug id.", cli_help="", positional=True),
            Param(
                "reason",
                help="Why the finding is false (with reopen: why reopen).",
                cli_help="why the finding is false",
                required=True,
            ),
            Param(
                "evidence",
                help="The probe command or test node id that showed it false.",
                cli_help="the probe command or test node id that showed it",
                default="",
            ),
            Param("reopen", type="boolean", help="Reopen the closed bug instead.", mcp_only=True),
        ),
    ),
    Command(
        path=("bug", "reopen"),
        via=("ddflow_bug_invalid", "reopen"),
        summary="reopen a bug closed by mistake (fixed or invalid), saying why",
        params=(
            Param("id", positional=True),
            Param("reason", help="why the closure was wrong", required=True),
        ),
    ),
    Command(
        path=("session", "start"),
        tool="ddflow_session_start",
        description="Open a session for provenance logging. Returns the session id.",
        call=lambda repo, a, agent: _api().session_start(
            repo, model=a.get("model", "") or "", tool=a.get("tool", "") or "", agent=agent
        ),
        payload=("session",),
        params=(
            Param("model", help="Your model id.", cli_help="", default=""),
            Param("tool", help="Your harness, e.g. 'claude-code'.", cli_help="", default=""),
        ),
    ),
    Command(
        path=("session", "prompt"),
        tool="ddflow_session_prompt",
        description="Record the operator's prompt verbatim. This is what makes the project "
        "reconstructible from the log alone if everything else is lost. Secrets are "
        "redacted before anything touches disk. Call it once per operator turn.",
        call=lambda repo, a, agent: _api().session_prompt(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        payload=("redactions", "session", "how"),
        params=(
            Param(
                "session",
                help="Session id; omit for the latest open.",
                cli_help="the SESSION id (from `session start`), not the text; omitted: the latest open session, else an implicit new one",
                positional=True,
                nargs="?",
                default="",
            ),
            Param(
                "text",
                help="The prompt, verbatim.",
                cli_help="the prompt text; without it, read from piped stdin",
                tool_required=True,
            ),
            Param("item", help="Item it concerns.", cli_help="", default=""),
        ),
    ),
    Command(
        path=("session", "note"),
        tool="ddflow_session_note",
        description="Record something that happened during a session which is neither an "
        "operator prompt nor a decision — a surprise, a dead end, why you changed "
        "approach. It goes into the reconstruction alongside the prompts, and a "
        "dead end recorded is a dead end nobody walks down twice.",
        call=lambda repo, a, agent: _api().session_note(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        payload=("session", "how"),
        params=(
            Param(
                "session",
                help="Session id; omit for the latest open.",
                cli_help="the SESSION id (from `session start`), not the text; omitted: the latest open session, else an implicit new one",
                positional=True,
                nargs="?",
                default="",
            ),
            Param(
                "text",
                help="The note.",
                cli_help="the note text; without it, read from piped stdin",
                tool_required=True,
            ),
            Param("item", help="Item it concerns.", cli_help="", default=""),
        ),
    ),
    Command(
        path=("session", "adopt-orphans"),
        reason="a one-off backfill an operator runs after ddflow doctor names id-less prompts; agents record with ddflow_session_prompt, which never lacks a session now",
        summary="attach prompts/notes recorded with no session id to a session",
        params=(),
    ),
    Command(
        path=("session", "end"),
        tool="ddflow_session_end",
        description="Close a session with a summary of what it achieved. The summary is what a "
        "later reader sees before deciding whether to open the whole transcript, "
        "so write it for someone who was not there.",
        call=lambda repo, a, agent: _api().session_end(
            repo, a.get("session", "") or "", summary=a.get("summary", "") or "", agent=agent
        ),
        payload=("session",),
        params=(
            Param("session", help="Session id.", cli_help="", positional=True),
            Param("summary", help="What this session achieved.", cli_help="", default=""),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
