"""The record-keeping commands, part: the bug commands that close or relabel: fixed, invalid, tag, reopen."""

from __future__ import annotations

from ._shared import (
    BUG_FIXED_FIELDS,
    BUG_INVALID_FIELDS,
    MUTATION_FLAG_EXEMPT,
    MUTATION_ORDER,
    MUTATION_PARAMS,
    OK,
    CliPolicy,
    Command,
    Param,
    _api,
    _bug_reopen,
    _bug_tag,
    _regression_tests,
    _reopening,
    _tagging,
    mutation_of,
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("bug", "fixed"),
        tool="ddflow_bug_fixed",
        flag_exempt=MUTATION_FLAG_EXEMPT,
        description="Close a bug. Requires the regression test that would catch it again: "
        "write it, watch it FAIL against the unfixed code, then close. "
        "`tag`: instead add/remove the tags (`add`, `remove`) of the open or closed bug `id`.",
        call=lambda repo, a, agent: (
            _bug_tag(repo, a, agent=agent)
            if _tagging(a)
            else _api().bug_fixed(
                repo,
                a["id"],
                regression_test=_regression_tests(a),
                lesson=a.get("lesson", "") or "",
                lesson_title=a.get("lesson_title", "") or "",
                lesson_rule=a.get("lesson_rule", "") or "",
                changelog=a.get("changelog", "") or "",
                verify_regression=not a.get("skip_regression_verify", False),
                verify_reason=a.get("verify_reason", "") or "",
                mutation=mutation_of(a),
                landed=a.get("landed", "") or "",
                lint_exempt=a.get("lint_exempt", "") or "",
                agent=agent,
            )
        ),
        payload=(
            *BUG_FIXED_FIELDS,
            "tags",
            "was",
            "changed",
        ),
        params=(
            Param("id", help="Bug id.", cli_help="", positional=True),
            Param(
                "regression_test",
                help="Test that now guards this. Several: separate with ',' or ';'.",
                cli_help="the test that now guards this bug; repeat it, or separate with ',' or ';', for several",
                action="append",
                default=[],
            ),
            Param(
                "skip_regression_verify",
                type="boolean",
                help="Skip the pre-fix check (the test must FAIL pre-fix, PASS fixed). Needs verify_reason.",
                cli_help="do not run the named test on the pre-fix tree; requires --verify-reason (recorded)",
            ),
            Param(
                "verify_reason",
                help="Why the pre-fix check is skipped (recorded); required with skip_regression_verify.",
                cli_help="why the pre-fix regression check is skipped (recorded)",
                default="",
            ),
            *MUTATION_PARAMS,
            Param(
                "landed",
                help="A commit already on the base that holds the fix (the fix task was claimed after it landed): the test must fail on its first parent and pass on it; the fix task is ended naming it.",
                cli_help="a commit already on the base that holds the fix: the test must fail on its first parent and pass on it; the bug's fix task is ended naming it",
                default="",
            ),
            Param(
                "lint_exempt",
                help="For a CI-filed LINT bug (a check that is not a test, e.g. ruff format --check): why it needs no regression test (what made the check green). Recorded as regression_verified=exempt-lint; instead of regression_test.",
                cli_help="close a CI-filed lint bug (no test to write) with this reason, recorded, instead of --regression-test",
                default="",
            ),
            Param(
                "lesson",
                help="Id of an existing lesson this bug belongs to.",
                cli_help="",
                default="",
            ),
            Param(
                "lesson_title", help="Capture a lesson at the same time.", cli_help="", default=""
            ),
            Param(
                "lesson_rule",
                help="The lesson in full: the transferable rule, not the incident.",
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
                help="The tests guarding this, one per element: the list form of regression_test.",
                mcp_only=True,
            ),
            Param(
                "tag",
                type="boolean",
                help="Tag the bug instead of closing it: add/remove tags of the open or closed `id`.",
                mcp_only=True,
            ),
            Param("add", help="With tag: comma-separated tags to add.", mcp_only=True),
            Param("remove", help="With tag: comma-separated tags to remove.", mcp_only=True),
        ),
        tool_order=(
            "id",
            "tag",
            "add",
            "remove",
            "regression_test",
            "regression_tests",
            "lesson_title",
            "lesson_rule",
            "lesson",
            "changelog",
            "skip_regression_verify",
            "verify_reason",
            "landed",
            "lint_exempt",
            *MUTATION_ORDER,
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
            else BUG_INVALID_FIELDS
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
        path=("bug", "tag"),
        via=("ddflow_bug_fixed", "tag"),
        summary="add or remove tags on an existing bug, open or closed",
        call=lambda repo, a, agent: _bug_tag(repo, a, agent=agent),
        payload=("id", "tags", "was", "changed"),
        render=lambda out, a: (
            f"bug {a['id']} tags: {', '.join(out.data['tags']) or '(none)'}"
            + ("" if out.data["changed"] else " (unchanged)")
        ),
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", positional=True),
            Param("add", help="comma-separated tags to add", default=""),
            Param("remove", help="comma-separated tags to remove", default=""),
        ),
    ),
    Command(
        path=("bug", "reopen"),
        via=("ddflow_bug_invalid", "reopen"),
        summary="reopen a bug closed by mistake (fixed or invalid), saying why",
        call=lambda repo, a, agent: _bug_reopen(repo, a, agent=agent),
        payload=(
            "id",
            "was",
            "reason_given",
            "fix_task",
            "fix_task_state",
            "previous_fix_task",
            "next",
        ),
        render=lambda out, a: (
            f"bug {a['id']} reopened (was {out.data['was']}): {out.data['reason_given']}\n"
            f"{out.data['next']}"
        ),
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", positional=True),
            Param("reason", help="why the closure was wrong", required=True),
        ),
    ),
)
