"""The record-keeping commands, part: research and the first bug commands: found, file-tasks, dedupe, duplicate."""

from __future__ import annotations

from ._shared import (
    ANSWER_FLAG_EXEMPT,
    ANSWER_PARAMS,
    Command,
    Param,
    _answer,
    _api,
    _bug_dedupe_render,
    _bug_status_render,
    file_tasks_call,
    importlib,
)

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
            # `ddflow research add ...` as well as `ddflow research ...`: `lesson add`,
            # `decision add` and `memory add` all take the verb, the MCP tool is
            # `ddflow_research_add`, and the research gate's instruction says `research add`
            # -- which this parser rejected as "unrecognized arguments: add" for every agent
            # that followed it.
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
        flag_exempt={
            **ANSWER_FLAG_EXEMPT,
            "--test": "CLI only: the tools list has no room (DX-bug-repro-gate-and-mcp)",
            "--repro-cmd": "CLI only: the tools list has no room (DX-bug-repro-gate-and-mcp)",
            "--repro-capture": "CLI only; over MCP set [bugs] repro_capture with ddflow_configure",
        },
        description="Report a bug the moment you find it, BEFORE fixing it. Recording it first "
        "makes the fix accountable: `ddflow_bug_fixed` refuses to close one without "
        "a regression test.",
        call=lambda repo, a, agent: _api().bug_found(
            repo,
            summary=a.get("summary", "") or "",
            item=a.get("item", "") or "",
            id=a.get("id", "") or "",
            title=a.get("title", "") or "",
            severity=a.get("severity", "") or "",
            scope=a.get("scope", "") or "",
            globs=a.get("globs", "") or "",
            tags=a.get("tags", "") or "",
            no_task=bool(a.get("no_task")),
            answer=_answer(a),
            agent=agent,
            evidence=a.get("evidence", "") or "",
        ),
        payload=("id", "offer", "fix_task", "fix_task_filed", "glob_warnings"),
        params=(
            Param(
                "id",
                help="Stable id, e.g. 'B1'. You will cite it when closing.",
                cli_help="",
                default="",
            ),
            Param("summary", help="What is wrong, in one line.", cli_help="", required=True),
            Param(
                "evidence",
                help="What showed the bug: evidence kept with the record (agent-supplied "
                "text, stored, never run).",
                mcp_only=True,
                default="",
            ),
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
                "tags",
                help="Comma-separated tags; plain strings, `ns:x` is one tag, `wave:*` is reserved.",
                cli_help="comma-separated tags (plain strings; a namespaced ns:x is one tag; wave:* is reserved)",
                default="",
            ),
            Param(
                "no_task",
                type="boolean",
                help="File no fix task (fixed in the same commit).",
                cli_help="file no fix task: the bug is fixed in the commit that found it",
            ),
            Param(
                "repro_cmd",
                cli_only=True,
                cli_help="the command that shows the bug, run once at filing in a scratch "
                "checkout of the recorded sha (CLI only; the operator's own command)",
                default="",
            ),
            Param(
                "test",
                cli_only=True,
                cli_help="a pytest id: run once, its output tail goes in the repro bundle",
                default="",
            ),
            Param(
                "repro_capture",
                cli_only=True,
                cli_help="off | basic | full: how much repro bundle to store (default [bugs] repro_capture)",
                default="",
            ),
            *ANSWER_PARAMS,
        ),
    ),
    Command(
        path=("bug", "file-tasks"),
        summary="file the fix task of one open bug that has none (--all: every such bug)",
        tool="ddflow_bug_file_tasks",
        description="File the fix task of ONE open bug that has none (`id`), or of every such "
        "bug (`all`: one-shot after an upgrade -- other agents' bugs too). With neither it "
        "only PREVIEWS (`dry_run` true in the answer) and files nothing. "
        "`ddflow_bug_found` files one per bug by default.",
        call=file_tasks_call,
        payload=("filed", "linked", "tasks", "links", "dry_run", "preview"),
        params=(
            Param(
                "id",
                help="The open bug to file the fix task of.",
                cli_help="the open bug to file the fix task of (omit to preview; --all files every bug's)",
                positional=True,
                nargs="?",
                default="",
            ),
            Param(
                "all",
                type="boolean",
                help="File a fix task for EVERY open bug that has none, other agents' too. Without it and without `id`, only a preview.",
                cli_help="file a task for every open bug that has none (other agents' too); without it and without an id, only a preview",
            ),
            Param(
                "dry_run",
                type="boolean",
                help="List what would be filed; write nothing.",
                cli_help="list what would be filed",
            ),
        ),
    ),
    Command(
        path=("bug", "dedupe"),
        summary="cluster near-duplicate OPEN bugs by symptom + root cause; --apply links the younger ones onto each cluster's canonical",
        tool="ddflow_bug_dedupe",
        family="ddflow_bug",
        action="dedupe",
        tier="standard",
        description="Cluster the OPEN bugs that describe one root cause under different "
        "wordings (symptom + root-cause matching: the TF-IDF overlap engine plus the "
        "semantic embedder when [rag] names one), and report each cluster's canonical "
        "bug id, its duplicate ids and the shared cause. apply: link each younger "
        "duplicate onto its cluster's canonical member with `ddflow link --duplicate-of` "
        "-- nothing is ever closed here; a duplicate bug is closed only once its "
        "original is fixed. Nothing close: exit 2.",
        call=lambda repo, a, agent: _api().dedupe_bugs(
            repo,
            apply=bool(a.get("apply")),
            floor=a.get("floor"),
            agent=agent,
        ),
        payload=("clusters", "count", "applied", "floor", "semantic", "scanned"),
        render=_bug_dedupe_render,
        wire_shape=(("bug", "dedupe"), {}),
        params=(
            Param(
                "apply",
                type="boolean",
                help="Link each younger duplicate onto its cluster's canonical member (a `duplicate_of` link; nothing is closed).",
                cli_help="link each younger duplicate onto its cluster's canonical (nothing is closed)",
            ),
            Param(
                "floor",
                type="number",
                help="Minimum score at which two bugs cluster (default: [dedupe].show_floor).",
                cli_help="minimum score to cluster (default: [dedupe].show_floor)",
            ),
        ),
    ),
    Command(
        path=("bug", "status"),
        summary="how many open bugs: totals by severity and blocked vs unblocked",
        tool="ddflow_bug_status",
        family="ddflow_bug",
        action="status",
        tier="standard",
        description="How many bugs are open, counted by severity (critical/high/medium/low/unset), "
        "and how many of them are blocked (their fix task is blocked or still has unmet needs), "
        "unblocked, or have no fix task yet. Read-only summary of the bug backlog.",
        call=lambda repo, a, agent: importlib.import_module("ddflow.api.knowledge.bugs").bug_status(
            repo
        ),
        payload=("open", "severity", "blocked", "unblocked", "no_fix_task"),
        render=_bug_status_render,
        wire_shape=(("bug", "status"), {}),
    ),
    Command(
        path=("bug", "duplicate"),
        summary="close a bug as a duplicate of a bug that is already FIXED (cites its test)",
        tool="ddflow_bug_duplicate",
        family="ddflow_bug",
        action="duplicate",
        tier="standard",
        description="Close the open bug `id` as a duplicate of `of`, which must already be "
        "FIXED with a regression test: the link is recorded and the closure cites that "
        "test. Refused (exit 3) while `of` is open, fixed without a test, unknown, or `id` "
        "is closed. `bug fixed` lists the open duplicates of the bug it closes; nothing "
        "closes automatically.",
        call=lambda repo, a, agent: importlib.import_module("ddflow.api.knowledge").bug_duplicate(
            repo, a["id"], of=a.get("of", "") or "", agent=agent
        ),
        payload=("id", "of", "regression_test", "fix_task", "fix_task_removed"),
        render=lambda out, a: (
            f"bug {a['id']} closed as a duplicate of {out.data['of']} "
            f"(guarded by {out.data['regression_test']})"
        ),
        params=(
            Param("id", help="The open duplicate bug to close.", cli_help="", positional=True),
            Param(
                "of",
                help="The FIXED bug it duplicates.",
                cli_help="the already-fixed bug this one duplicates",
                required=True,
            ),
        ),
    ),
)
