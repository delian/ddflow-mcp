"""MCP tools: lessons, research, and closing bugs.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _answer, _api, _bug_reopen, _regression_tests, _reopening

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_lesson_verify": {
        "description": (
            "Re-scan every lesson that declared a code `pattern` and report the sites where "
            "it has REAPPEARED. Exit 1 names them; exit 2 means no lesson declares a "
            "pattern, which is NOT a pass — it means this project has no mechanical ratchet "
            "on its lessons yet. Run after a change that touches code a lesson governs."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().lessons_verify(repo, agent=agent),
        "payload": "text",
        "text": True,
    },
    "ddflow_lesson_add": {
        "description": (
            "Record a lesson so it is never re-learned. Use after any bug, any operator "
            "correction, any surprise. Make the rule transferable — a future agent on a "
            "different task must be able to apply it."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                False,
            ),
            "title": ("string", "The rule as a one-line statement.", True),
            "rule": ("string", "The rule in full.", False),
            "why": ("string", "Why it is true / what went wrong.", False),
            "how": ("string", "How to apply or detect it.", False),
            "summary": (
                "string",
                "The lesson in ONE paragraph, for a reader who will not open the full rule. Rendered into docs/ddflow/LESSONS-SUMMARY.md.",
                False,
            ),
            "tags": ("string", "Comma-separated tags.", False),
            "seen_in": (
                "string",
                "Comma-separated item ids where this was hit. What makes a lesson "
                "checkable later instead of merely memorable.",
                False,
            ),
            "supersedes": (
                "string",
                "Comma-separated lesson ids this replaces. The old one is retired, not deleted: the corpus stops growing without losing what was once believed.",
                False,
            ),
            "pattern": (
                "string",
                "A regex naming the mistake in CODE. Scans now and stores WHICH sites match, so `ddflow_lesson_verify` can name those that reappear. Prefer it to a remembered rule when mechanical: a count says 'worse', never 'which'. Refused if it does not compile.",
                False,
            ),
            "globs": (
                "string",
                "Comma-separated globs to scan for `pattern`. Default: every tracked file.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().lesson_add(
            repo,
            _api().LessonDraft(
                title=a.get("title", "") or "",
                rule=a.get("rule", "") or "",
                why=a.get("why", "") or "",
                how=a.get("how", "") or "",
                summary=a.get("summary", "") or "",
                tags=a.get("tags", "") or "",
                seen_in=a.get("seen_in", "") or "",
                supersedes=a.get("supersedes", "") or "",
                pattern=a.get("pattern", "") or "",
                globs=a.get("globs", "") or "",
                id=a.get("id", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_lesson_search": {
        "description": (
            "Search past lessons by relevance (BM25). Use before starting "
            "work, and whenever something surprises you."
        ),
        "properties": {
            "query": ("string", "What you are about to do, in words.", True),
            "limit": ("integer", "Max results (default 5).", False),
        },
        "api": lambda repo, a, agent: _api().lesson_search(
            repo, a.get("query", "") or "", limit=a.get("limit"), agent=agent
        ),
        "payload": "hits",
    },
    "ddflow_research_add": {
        "description": (
            "Record a research finding. verdict MUST be CONFIRMED, REFUTED or "
            "THEORETICAL, and CONFIRMED/REFUTED require a probe — a verdict with no "
            "probe behind it is an opinion. A REFUTED entry is as valuable as an "
            "adopted one: it stops the next session re-researching it."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                False,
            ),
            "question": ("string", "What was asked.", True),
            "verdict": ("string", "CONFIRMED | REFUTED | THEORETICAL", True),
            "claim": ("string", "The falsifiable claim.", False),
            "falsifier": ("string", "The single observation that would kill it.", False),
            "probe": ("string", "The command you ran.", False),
            "probe_output": ("string", "Its output, verbatim.", False),
            "sources": ("string", "Comma-separated URLs/DOIs you actually opened.", False),
            "mechanism": (
                "string",
                "WHY it would work in this repo. The middle field of the triple — "
                "claim, mechanism, falsifier — and the one most often skipped.",
                False,
            ),
            "budget": ("string", "What you allowed yourself, e.g. '30 min, no GPU'.", False),
            "item": ("string", "The task this research is for.", False),
        },
        "api": lambda repo, a, agent: _api().research_add(
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
        "payload": ("id", "verdict"),
    },
    "ddflow_bug_fixed": {
        "description": (
            "Close a bug. Requires the name of the regression test that would "
            "catch it again — write the test, watch it FAIL against the "
            "unfixed code, then close."
        ),
        "properties": {
            "id": ("string", "Bug id.", True),
            "regression_test": (
                "string",
                "Test that now guards this. Several: separate them with ',' or ';', or "
                "pass regression_tests.",
                False,
            ),
            "regression_tests": (
                "array",
                "The tests that now guard this, one per element -- the list form of "
                "regression_test. One of the two is required.",
                False,
            ),
            "lesson_title": ("string", "Capture a lesson at the same time.", False),
            "lesson_rule": (
                "string",
                "The lesson in full — the transferable rule, not the incident. A "
                "future agent on a different task has to be able to apply it.",
                False,
            ),
            "lesson": ("string", "Id of an EXISTING lesson this bug belongs to.", False),
            "changelog": ("string", "Optional 'Fixed: text' (any category), or skip.", False),
            "skip_regression_verify": (
                "boolean",
                "Do NOT run the named test on the pre-fix tree (default false: it is run "
                "and must FAIL there, PASS with the fix). Needs verify_reason.",
                False,
            ),
            "verify_reason": (
                "string",
                "Why the pre-fix regression check is skipped (recorded). Required when "
                "skip_regression_verify is true.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().bug_fixed(
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
        "payload": ("id", "regression_verified", "lesson_capture"),
    },
    "ddflow_bug_invalid": {
        "description": (
            "Close a bug as a FALSE finding -- nothing was broken, so nothing was fixed. "
            "Never counts as a fix. Requires the reason; give the probe or test that "
            "showed it false as evidence. Refused (exit 3) for an unknown id or a bug "
            "already closed; a real bug is closed with `ddflow_bug_fixed` instead. "
            "`reopen`: instead UNDO a closure (fixed or invalid) made by mistake."
        ),
        "properties": {
            "id": ("string", "Bug id.", True),
            "reason": ("string", "Why the finding is false (with reopen: why reopen).", True),
            "evidence": (
                "string",
                "The probe command or test node id that showed it false.",
                False,
            ),
            "reopen": ("boolean", "Reopen the closed bug instead.", False),
        },
        "api": lambda repo, a, agent: (
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
        "payload": lambda a: (
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
    },
}
