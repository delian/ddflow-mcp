"""The flow commands, declared once: pr, version, promote and flow, with the tools they have.

`surfaces/parsers/flow.py` registers their command-line halves and `surfaces/tools/__init__.py`
takes their MCP entries (D-unify 4, 6f-flow). `version lint` has no tool, and says why.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api

#: The words a group is listed under in `ddflow --help`.
GROUPS: dict[str, str] = {
    "pr": "pull/merge requests: what reviewers did ([flow].integration = pr)",
    "version": "version tags: the next version, and cutting it",
    "promote": "environment branches: move work one step downstream ([flow].environments)",
    "flow": (
        "how this project works: branching model, release lines, and every workflow "
        "choice with who made it"
    ),
}

_BUMPS = ("", "major", "minor", "patch")

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("pr", "sync"),
        tool="ddflow_pr_sync",
        summary=(
            "ask the forge about every request in review: complete merged ones, reopen "
            "ones with requested changes, merge approved ones"
        ),
        description=(
            "Ask the forge (GitHub/GitLab) what reviewers did with every request in REVIEW and record it: a merged request completes its item (and retargets what is stacked on it), requested changes return the item to the queue WITH the review text, a closed one is parked for a person, an approved green one is merged when [flow].pr_merge = 'on_approval'. `ddflow_next` does this itself with [flow].sync_on_next. Exit 2: the forge could not be asked -- NOT 'nothing changed'."
        ),
        params=(
            Param(
                "item",
                help="Only this item (optional).",
                default="",
                cli_help="only this item",
            ),
        ),
        call=lambda repo, a, agent: _api().pr_sync(repo, item=a.get("item", "") or "", agent=agent),
        payload="",
    ),
    Command(
        path=("pr", "status"),
        tool="ddflow_pr_status",
        summary="every item's request, from the log (no forge call)",
        description=(
            "Every item's pull request as last recorded — review, checks, target, rounds "
            "of changes and when it was last looked at. Reads the log only; "
            "`ddflow_pr_sync` asks the forge."
        ),
        call=lambda repo, a, agent: _api().pr_status(repo, agent=agent),
        payload="",
    ),
    Command(
        path=("pr", "threads"),
        tool="ddflow_pr_threads",
        summary="an item's review threads from the forge; with --thread, reply and/or resolve one",
        description=(
            "An item's review threads, read live from the forge. With `thread`, `reply` on "
            "it and/or `resolve` it, so the reviewer sees what was addressed. Exit 2 = "
            "forge not reached; 3 = refused."
        ),
        params=(
            Param("id", help="The item.", positional=True, required=True, cli_help=""),
            Param(
                "thread",
                help="Thread id, as listed.",
                default="",
                cli_help="the thread's id (as listed)",
            ),
            Param(
                "reply",
                help="Reply text.",
                default="",
                cli_help="post this reply on --thread",
            ),
            Param(
                "resolve",
                type="boolean",
                help="Resolve it.",
                cli_help="mark --thread resolved",
            ),
        ),
        call=lambda repo, a, agent: _api().pr_threads(
            repo,
            a["id"],
            thread=a.get("thread", "") or "",
            reply=a.get("reply", "") or "",
            resolve=bool(a.get("resolve")),
            agent=agent,
        ),
        payload="",
    ),
    Command(
        path=("version", "show"),
        tool="ddflow_version_show",
        summary="current version, next version, why, release notes",
        description=(
            "The current version (highest `<tag_prefix>X.Y.Z` tag reachable from the "
            "release branch), the next one, the bump and why (Conventional Commits plus "
            "the tags of finished items), and the release notes. Reads only."
        ),
        params=(
            Param(
                "bump",
                help="Force major | minor | patch instead of the computed bump.",
                choices=_BUMPS,
                default="",
                cli_help="",
            ),
            Param(
                "line",
                help="A maintenance line (default: the current one).",
                default="",
                cli_help="a maintenance line (default: the current one)",
            ),
        ),
        call=lambda repo, a, agent: _api().version_show(
            repo, bump=a.get("bump", "") or "", line=a.get("line", "") or "", agent=agent
        ),
        payload="",
    ),
    Command(
        path=("version", "lint"),
        summary=(
            "is every knob and event-kind change in this tree announced in the upgrade manifest?"
        ),
        params=(
            Param(
                "waive",
                default="",
                metavar="CHANGE",
                cli_help=(
                    "let one unmanifested change (<kind>:<key>) ship, recorded with --reason: "
                    "the operator's decision"
                ),
            ),
            Param("reason", default="", cli_help="why --waive"),
        ),
        reason="the release lint runs inside ddflow_version_cut (also with dry_run), and its waiver is the operator's decision, from the CLI; a tool of its own would cost every client's tools/list for a check only a release-maker runs",
    ),
    Command(
        path=("version", "cut"),
        tool="ddflow_version_cut",
        summary="tag the next version (gitflow: via a release branch, or a release request)",
        description=(
            "Tag the next version. trunk: tags the base branch. gitflow: release/X from "
            "develop, merged to production, tagged, tag merged back; with pull requests, "
            "opens the release request (`ddflow_pr_sync` tags it once merged). Exit 2 = "
            "nothing to release."
        ),
        params=(
            Param("bump", help="major | minor | patch.", choices=_BUMPS, default="", cli_help=""),
            Param(
                "version",
                help="MAJOR.MINOR.PATCH.",
                flag="--version",
                metavar="SET_VERSION",
                default="",
                cli_help="exact MAJOR.MINOR.PATCH",
            ),
            Param(
                "push",
                type="boolean",
                help="Publish the tag and branches.",
                cli_help="publish the tag (and branches) to the remote",
            ),
            Param("dry_run", type="boolean", help="Report only.", cli_help=""),
            Param(
                "line",
                help="A maintenance line: tagged where it stands; its major is kept.",
                default="",
                cli_help="a maintenance line (default: the current one)",
            ),
            Param(
                "changelog",
                type="boolean",
                help="Also write CHANGELOG.md.",
                cli_help=(
                    "also write the version's section into CHANGELOG.md, committed with the cut "
                    "(in a release request under gitflow + pr); never written without this flag"
                ),
            ),
            Param(
                "force",
                type="boolean",
                help="Overwrite a hand-edited file.",
                cli_help="with --changelog: replace a hand-edited changelog",
            ),
        ),
        call=lambda repo, a, agent: _api().version_cut(
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
        payload="",
    ),
    Command(
        path=("promote", "add"),
        tool="ddflow_promote_add",
        summary="file a promotion to ENV from the branch just upstream of it",
        description=(
            "File a PROMOTION to an environment branch ([flow].environments): a task that "
            "merges the branch immediately upstream into it, runs the promotion pipeline "
            "and lands by merge or request -- always one step downstream. Exit 2 = nothing "
            "to promote; exit 3 = refused (unknown environment, one open, branch missing)."
        ),
        params=(
            Param(
                "env",
                help="The environment to promote TO.",
                positional=True,
                required=True,
                cli_help="",
            ),
            Param(
                "force",
                type="boolean",
                help="File it even with nothing to carry.",
                cli_help="file it even with nothing to carry",
            ),
        ),
        call=lambda repo, a, agent: _api().promote_add(
            repo, a["env"], force=bool(a.get("force")), agent=agent
        ),
        payload="",
    ),
    Command(
        path=("promote", "deployed"),
        tool="ddflow_promote_deployed",
        summary="record the sha a deploy put live in ENV (call it from the deploy hook)",
        description=(
            "Record the sha a deploy put LIVE in an environment (from the deploy hook); "
            "promote_status then shows what runs there. Exit 3 = refused."
        ),
        params=(
            Param("env", help="Environment.", positional=True, required=True, cli_help=""),
            Param(
                "sha",
                help="Deployed commit (default: branch head).",
                default="",
                cli_help="the deployed commit (default: ENV's branch head)",
            ),
        ),
        call=lambda repo, a, agent: _api().promote_deployed(
            repo, a["env"], sha=a.get("sha", "") or "", agent=agent
        ),
        payload="",
    ),
    Command(
        path=("promote", "status"),
        tool="ddflow_promote_status",
        summary="each environment: head, commits behind upstream, open promotion",
        description=(
            "Each environment: head, commits behind its upstream, open promotion, "
            "auto_promote, and the live (deployed) sha. Reads only."
        ),
        call=lambda repo, a, agent: _api().promote_status(repo, agent=agent),
        payload="",
    ),
    Command(
        path=("flow", "show"),
        tool="ddflow_flow_show",
        summary="every choice: value, options, and who decided",
        description=(
            "How THIS project works: its branching model, release lines, and every workflow "
            "choice (model, integration, pr_merge, port_strategy, ...) with its value, the "
            "options, and who decided -- the operator's config, a recorded choice, or a "
            "default nobody chose. `pending` lists relevant choices nobody has made: ask the "
            "operator, or pick what suits the project with `ddflow_flow_choose`."
        ),
        call=lambda repo, a, agent: _api().flow_show(repo, agent=agent),
        payload="",
    ),
    Command(
        path=("flow", "choose"),
        tool="ddflow_flow_choose",
        summary="record a workflow choice (the config file still wins over it)",
        description=(
            "Record a workflow choice for this project, attributed to you, with a reason the "
            "next agent will read. Make it when the operator told you, or when they left it "
            "to you -- a choice left unmade is defaulted at first use and followed from then "
            "on. The operator's config file wins over a recorded choice; the result says "
            "`in_effect: false` when it does."
        ),
        params=(
            Param(
                "knob",
                help="The choice, e.g. port_strategy (see ddflow_flow_show).",
                positional=True,
                required=True,
                cli_help="",
            ),
            Param(
                "value",
                help="One of its options.",
                positional=True,
                required=True,
                cli_help="",
            ),
            Param(
                "reason",
                help="Why this suits the project.",
                default="",
                cli_help="why — the next agent reads this",
            ),
        ),
        call=lambda repo, a, agent: _api().flow_choose(
            repo, a["knob"], a["value"], reason=a.get("reason", "") or "", agent=agent
        ),
        payload="",
    ),
)

BY_TOOL: dict[str, Command] = by_tool(COMMANDS)
