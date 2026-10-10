"""The export, bisect, tests, precommit and ci commands, declared once.

`surfaces/parsers/` registers their command-line halves and `surfaces/tools/` takes their MCP
entries (D-unify 4, B-uni-cmd-migrate.6i-export). Each tool offers fewer arguments than its
command takes (`tools/list` has a byte budget, and some flags are the operator's, at a
terminal): the command-line-only parameters are `cli_only`, and the flags a tool omits on
purpose are `flag_exempt`, each with its reason.
"""

from __future__ import annotations

from ...core.outcome import NOTHING
from ..registry import Command, Param, by_tool
from ..tools._common import _api, _bisect

#: Where `bisect` takes its candidates from when `--glob` is not given (`api.bisect.DEFAULT_GLOB`;
#: `tests/test_declared_export.py` holds the two together).
BISECT_DEFAULT_GLOB = "tests/**/test_*.py"


def _bisect_text(out, a) -> str:
    """`bisect`'s report: the polluters, or why there is nothing to report, then each run."""
    d = out.data
    lines = [f"victim: {d['victim']}", f"candidates before it: {d['candidates']}", ""]
    if d["state"] == "found":
        lines.append("Run before the victim, these make it fail (remove any one and it passes):")
        lines += [f"  {p}" for p in d["polluters"]]
    else:
        lines.append(f"Nothing to report ({d['state']}): {d['summary']}")
        if d["polluters"]:
            lines.append("Smallest set that still fails so far:")
            lines += [f"  {p}" for p in d["polluters"]]
    lines += ["", f"{len(d['runs'])} run(s)"]
    for r in d["runs"]:
        note = f" -- {r['detail']}" if r["detail"] and r["outcome"] != "passed" else ""
        lines.append(f"  {r['outcome']:<13s} {r['tests']:>4d} test(s)  {r['seconds']}s{note}")
    return "\n".join(lines)


def _tests_text(out, a) -> str:
    """`tests`: what the change reaches, the command to run it, and the unit_tests gate's mode."""
    d = out.data
    if a.get("flakes") and "flakes_text" in d:
        return d["flakes_text"]
    if out.exit == NOTHING:
        lines = [out.reason]
    else:
        lines = [
            f"{len(d['tests'])} test file(s) reach {len(d['changed'])} changed file(s) since {d['base']}:"
        ]
        lines += [f"  {t['path']}  -- {t['reason']}" for t in d["tests"]]
        if d["command"]:
            lines.append(f"\nRun them now, in parallel:\n  {d['command']}")
    g = d.get("unit_tests_gate") or {}
    if g.get("scope") == "selected":
        lines.append(f"\nThe unit_tests gate runs only the selected tests ({g['why']}):")
        lines += [f"  {t['path']}  -- {t['reason']}" for t in g["tests"]]
        lines.append(f"  {g['command']}")
    elif g:
        lines.append(f"\nThe unit_tests gate runs the whole suite: {g['why']}\n  {d['full_suite']}")
    elif d["full_suite"]:
        lines.append(
            "\nThe unit_tests gate may run the whole suite; pass --item <id> to see which "
            f"mode it would use for that item:\n  {d['full_suite']}"
        )
    if d["advice"]:
        lines.append(f"  NOTE: that command {d['advice']}")
    return "\n".join(lines)


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("export",),
        tool="ddflow_export",
        summary="documents generated from the log: list, print, enable, disable, --diff, --check, --update",
        description=(
            "Documents from the log (roadmap, bugs, status, worklog, sessions, decisions, rules, "
            "changelog). No doc: list. doc: capped markdown (`truncated`). Writes only with "
            "write=true AND a repo path. action: list|enable|disable|validate (enable names you "
            "and how to stop it)."
        ),
        params=(
            Param(
                "doc",
                help="Kind; omit to list.",
                positional=True,
                nargs="?",
                default="",
                cli_help="the document kind; omit to list the kinds. Or a verb: enable <doc>, disable <doc>, ack, eject <doc>, validate [<doc>]",
            ),
            Param(
                "target",
                positional=True,
                nargs="?",
                default="",
                cli_only=True,
                cli_help="the document, after a verb",
            ),
            Param(
                "path",
                help="Repo path.",
                default="",
                cli_help="enable: the target file (repo-relative)",
            ),
            Param(
                "mode",
                help="enable: whole|region|append.",
                default="",
                cli_help="enable: whole, region or append",
            ),
            Param(
                "local",
                type="boolean",
                cli_only=True,
                cli_help="enable/disable: this machine only",
            ),
            Param(
                "lock",
                type="boolean",
                cli_only=True,
                cli_help="disable: the operator's veto; agents cannot enable it",
            ),
            Param(
                "all",
                type="boolean",
                help="The selected documents.",
                cli_help="act on the selected documents",
            ),
            Param(
                "since",
                help="From YYYY-MM-DD.",
                default="",
                cli_help="entries at or after this date (YYYY-MM-DD or a timestamp prefix)",
            ),
            Param(
                "version",
                help="One release.",
                default="",
                cli_help="one release (the changelog kind: X or vX, or unreleased)",
            ),
            Param("phase", help="One phase.", default="", cli_help="one phase"),
            Param(
                "item",
                help="Bugs item.",
                default="",
                cli_help="one item (the bugs document filters it as a phase)",
            ),
            Param(
                "status", help="e.g. open.", default="", cli_help="one status, e.g. open or fixed"
            ),
            Param("tag", help="One tag.", default="", cli_help="one tag"),
            Param("session", help="One session.", default="", cli_help="one session id"),
            Param(
                "limit",
                type="integer",
                help="At most N.",
                default=0,
                cli_help="at most N entries",
            ),
            Param(
                "max_bytes",
                type="integer",
                help="Max 60000.",
                default=None,
                cli_help="size cap for printing (default [export].max_bytes)",
            ),
            Param(
                "template",
                cli_only=True,
                default="",
                cli_help="render once with this template file; writes nothing",
            ),
            Param(
                "diff",
                type="boolean",
                help="Preview a write.",
                cli_help="show what a write would change",
            ),
            Param(
                "check",
                type="boolean",
                help="Is it fresh.",
                cli_help="exit 1 if the target is not what a write would produce",
            ),
            Param(
                "update",
                type="boolean",
                cli_only=True,
                cli_help="write the configured target (asks on a terminal)",
            ),
            Param(
                "out",
                cli_only=True,
                default="",
                cli_help="write to this repo-relative path instead",
            ),
            Param(
                "force",
                type="boolean",
                cli_only=True,
                cli_help="overwrite a hand-edited or unmarked target",
            ),
            Param(
                "yes",
                type="boolean",
                cli_only=True,
                cli_help="with --update: do not ask on a terminal",
            ),
            Param("action", help="list|enable|disable|validate.", mcp_only=True),
            Param("write", type="boolean", help="Write to path.", mcp_only=True),
        ),
        tool_order=(
            "action",
            "mode",
            "doc",
            "all",
            "since",
            "version",
            "phase",
            "item",
            "status",
            "limit",
            "tag",
            "session",
            "max_bytes",
            "diff",
            "check",
            "write",
            "path",
        ),
        flag_exempt={
            "--update": "MCP writes with write=true plus a repo-relative path",
            "--out": "MCP: write=true plus path (the same path-safety rules)",
            "--force": "overriding hand-edit protection is the operator's, at a terminal",
            "--template": "an agent never feeds the renderer an arbitrary file",
            "--lock": "the operator's veto: a person at a terminal locks a document",
            "--local": "a per-machine selection is the operator's, at a terminal",
            "--yes": "answers the terminal confirmation, which MCP has none of",
        },
        call=lambda repo, a, agent: _api().export_tool(repo, a, agent),
        payload="",
    ),
    Command(
        path=("bisect",),
        tool="ddflow_bisect",
        summary="find which earlier test file makes a test fail only in full-suite order (exit 0 = found, 2 = nothing to report)",
        description=(
            "Which earlier test file makes `victim` fail only in full-suite order? Delta-debugs the files before it, running `cmd` many times. Exit 2: nothing to report."
        ),
        params=(
            Param(
                "victim",
                help="Failing test id.",
                positional=True,
                required=True,
                cli_help="the test that fails only after others ran (a test id)",
            ),
            Param(
                "cmd",
                help="Test command; {tests} = the list.",
                required=True,
                cli_help="a command that runs a list of tests and exits non-zero on failure, with {tests} where the list goes, e.g. 'pytest -q {tests}'",
            ),
            Param(
                "candidates",
                help="Comma-separated files in run order.",
                default="",
                cli_help="comma-separated files, in run order",
            ),
            Param(
                "glob",
                cli_only=True,
                default="",
                cli_help=f"where candidates come from (default {BISECT_DEFAULT_GLOB})",
            ),
            Param(
                "timeout",
                type="integer",
                help="Seconds per run (600).",
                default=600,
                cli_type=float,
                cli_help="seconds per run (default 600)",
            ),
            Param(
                "repeat",
                type="integer",
                cli_only=True,
                default=1,
                cli_help="runs per probe; any failure counts",
            ),
            Param(
                "max_runs",
                type="integer",
                cli_only=True,
                default=200,
                cli_help="stop after this many runs",
            ),
        ),
        tool_order=("victim", "cmd", "candidates", "timeout"),
        flag_exempt={
            "--glob": "where candidates come from stays the default tests/**/test_*.py over MCP; an agent names `candidates` when the suite lives elsewhere (tools/list byte budget)",
            "--repeat": "re-running each probe is a terminal-side choice for a flaky pollution (tools/list byte budget)",
            "--max-runs": "the run budget is the operator's, set at a terminal; the default 200 bounds an agent's call (tools/list byte budget)",
        },
        call=lambda repo, a, agent: _bisect(repo, a),
        payload=("state", "victim", "polluters", "candidates", "summary", "runs"),
        render=_bisect_text,
    ),
    Command(
        path=("tests",),
        tool="ddflow_tests",
        summary="the tests your change reaches, and a parallel command to run them (exit 2 = none)",
        description=(
            "AFTER EACH CHANGE: the tests your change reaches (changed tests, tests importing a changed module directly or one step removed, tests named after it, a changed conftest's or data file's), each with why, and a command running them IN PARALLEL. Run it; do not reason about which matter. Never a pass: the WHOLE suite still runs before merge. `item`: diff that item's worktree. Exit 2: no test reaches the change."
        ),
        params=(
            Param(
                "item",
                help="The item whose worktree and base to use.",
                default="",
                cli_help="an item id: use its worktree and base",
            ),
            Param(
                "base",
                help="Compare against this ref instead of the item's base.",
                default="",
                cli_help="compare against this ref (default: the base)",
            ),
            Param(
                "flakes",
                type="boolean",
                cli_only=True,
                cli_help="show the flake log instead: every test that failed and then passed on the same tree through ddflow on this machine (.ddflow/local/flakes.jsonl)",
            ),
        ),
        flag_exempt={
            "--flakes": "the flake log is machine-local history for the operator's terminal; an agent reads `failed_tests` in the gate evidence (tools/list byte budget)",
        },
        call=lambda repo, a, agent, called_from=None: _api().relevant_tests(
            repo,
            flakes=bool(a.get("flakes")),
            item=a.get("item", "") or "",
            where=called_from,
            base=a.get("base", "") or "",
            agent=agent,
        ),
        payload="",
        render=_tests_text,
        # Without `item`, the diff is the caller's own checkout -- the worktree it is
        # standing in, not the primary the server was started on.
        wants_called_from=True,
    ),
    Command(
        path=("precommit",),
        tool="ddflow_precommit",
        summary="propose a .pre-commit-config.yaml for this repository's stacks (writes nothing without --write)",
        description=(
            "A .pre-commit-config.yaml proposed for THIS repository: its stacks (Python, shell, Docker, JS, Go, Rust; YAML/TOML/JSON checks) mapped to pinned hooks, plus ddflow's check-commit and check-msg as local hooks, so pre-commit owns .git/hooks/ (remove ddflow's own first; the body names them). Proposes; installs nothing. `write` creates the file, REFUSED (exit 3) when one exists. The body names missing programs. Installing pre-commit is the operator's call."
        ),
        params=(
            Param(
                "ddflow_cmd",
                help="How the local hooks reach ddflow (default `ddflow` on PATH).",
                default="ddflow",
                cli_help="how the proposed local hooks reach ddflow (default: `ddflow` on PATH)",
            ),
            Param(
                "write",
                type="boolean",
                help="Create the file; never replaces an existing one.",
                cli_help="create .pre-commit-config.yaml; an existing one is never replaced (exit 3)",
            ),
        ),
        call=lambda repo, a, agent, called_from=None: _api().precommit(
            repo,
            where=called_from,
            # Absent means the default; an empty string is passed on, and refused, as on
            # the CLI.
            ddflow_cmd="ddflow" if a.get("ddflow_cmd") is None else a["ddflow_cmd"],
            write=bool(a.get("write")),
            agent=agent,
        ),
        payload="",
        # The proposal is for the checkout the caller stands in, not the primary.
        wants_called_from=True,
    ),
    Command(
        path=("ci",),
        tool="ddflow_ci",
        summary="the CI parity gate: run the pre-push checks on the merge result",
        description="CI parity: run the pre-push checks on the branch merged with the base (run) or show what would run (status).",
        params=(
            Param(
                "verb",
                positional=True,
                nargs="?",
                default="status",
                choices=("run", "status", "record"),
                cli_only=True,
                cli_help="run: check the merge result; status (default): what would run, and whether it can",
            ),
            Param(
                "ref",
                help="Commit to check (run).",
                default="",
                cli_help="run: the commit or item id (default: HEAD here)",
            ),
            Param(
                "base",
                help="Branch merged in first (run).",
                default="",
                cli_help="run: merge this branch in first (default [ci].base)",
            ),
            Param(
                "command",
                help="Override [ci].command (run).",
                default="",
                cli_help="run: use this instead of [ci].command",
            ),
            Param(
                "stage",
                cli_only=True,
                default="pre-push",
                cli_help="record: gate | merge | pre-push | schedule",
            ),
            Param(
                "result",
                cli_only=True,
                default="",
                choices=("passed", "failed"),
                cli_help="record: how it went",
            ),
            Param(
                "report",
                cli_only=True,
                default="",
                cli_help="record: file with the pre-commit output, parsed for the failing checks",
            ),
            Param("sha", cli_only=True, default="", cli_help="record: the commit that was checked"),
            Param("action", help="run | status (default).", mcp_only=True),
        ),
        tool_order=("action", "ref", "base", "command"),
        flag_exempt={
            "--stage": "`ci record` is for the pre-push hook script, which has a shell and no MCP session",
            "--result": "`ci record`: see --stage",
            "--report": "`ci record`: see --stage",
            "--sha": "`ci record`: see --stage",
        },
        call=lambda repo, a, agent: _api().ci_tool(
            repo,
            action=a.get("action", "status") or "status",
            ref=a.get("ref", "HEAD") or "HEAD",
            base=a.get("base", "") or "",
            command=a.get("command", "") or "",
            agent=agent,
        ),
        payload="",
    ),
)

BY_TOOL = by_tool(COMMANDS)
