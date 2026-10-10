"""The queue commands, declared once: init, phase and task add, split, resolve and update.

`surfaces/parsers/queue.py` registers their command-line half and `surfaces/tools/` takes
their MCP entries (D-unify 4, B-uni-cmd-migrate.5-lifecycle). `init` has no tool of its own:
`ddflow_setup` covers it.
"""

from __future__ import annotations

from ...core.defaults import DEFAULT_PRIORITY
from ...core.outcome import OK
from ..argtypes import GLOBS_HELP, _Globs
from ..registry import CliPolicy, Command, Param, by_tool
from ..tools._common import _answer, _api, _list_or_none
from . import queue_cli as L
from .answer import ANSWER_FLAG_EXEMPT, ANSWER_PARAMS

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("init",),
        reason="covered by ddflow_setup",
        via=("ddflow_setup",),
        summary="create .ddflow/ in this repository",
        params=(),
    ),
    Command(
        path=("phase", "add"),
        tool="ddflow_phase_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Add a phase to the queue. A phase is a unit of REVIEW: it gets its own "
        "research, its own whole-phase test pass and live smoke run, and it merges "
        "as one coherent feature. Group tasks into a phase when they only make "
        "sense shipped together.",
        call=lambda repo, a, agent: _api().phase_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(DEFAULT_PRIORITY if a.get("priority") is None else a["priority"]),
            line=a.get("line", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        payload=("id",),
        params=(
            Param("id", help="Short stable id, e.g. 'P2' or 'auth'.", cli_help="", positional=True),
            Param("title", help="One-line description.", cli_help="", default=""),
            Param("needs", help="Comma-separated ids this phase depends on.", cli_help=""),
            Param(
                "globs",
                help="Comma-separated path globs this phase writes: what lets two agents work different phases in parallel; the phase's dependencies are INHERITED by every task in it.",
                cli_help=GLOBS_HELP,
                action=_Globs,
            ),
            Param("tags", help="Comma-separated tags.", cli_help=""),
            Param("body", help="Detail, acceptance criteria, context.", cli_help=""),
            Param(
                "priority",
                type="integer",
                help="Lower is offered first (default 100).",
                cli_help="",
                default=DEFAULT_PRIORITY,
            ),
            Param(
                "line",
                help="Release line this lands on (a name from [flow.lines], or the current line). Omit for the current line; tasks inherit a phase's line.",
                cli_help="release line (default: the current one)",
                default="",
            ),
            Param(
                "readd",
                type="boolean",
                help="File a REMOVED item's id again with this definition. An id still in the queue is always refused -- change that item with ddflow_update.",
                cli_help="file a REMOVED item's id again. An id still in the queue is always refused: change it with `ddflow update`",
            ),
            *ANSWER_PARAMS,
        ),
        tool_order=(
            "id",
            "title",
            "needs",
            "globs",
            "body",
            "tags",
            "priority",
            "line",
            "readd",
            "relation",
            "check_only",
        ),
    ),
    Command(
        path=("task", "add"),
        tool="ddflow_task_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Add a task to a phase. ALWAYS set globs to the paths this task will write: "
        "they are what lets two agents work in parallel safely, and an unset glob "
        "means the conflict detector cannot protect you.",
        call=lambda repo, a, agent: _api().task_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            parent=a.get("parent") or a.get("phase", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(DEFAULT_PRIORITY if a.get("priority") is None else a["priority"]),
            line=a.get("line", "") or "",
            lines=a.get("lines", "") or "",
            port_of=a.get("port_of", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        payload=("id", "line", "ports", "port_strategy", "defaulted"),
        params=(
            Param("id", help="Short stable id, e.g. 'P2.T1'.", cli_help="", positional=True),
            Param("title", help="One-line description.", cli_help="", default=""),
            Param("needs", help="Comma-separated ids this task depends on.", cli_help=""),
            Param(
                "globs",
                help="Comma-separated path globs this task writes.",
                cli_help=GLOBS_HELP,
                action=_Globs,
            ),
            Param("tags", help="Comma-separated tags.", cli_help=""),
            Param("body", help="Detail and acceptance criteria.", cli_help=""),
            Param(
                "priority",
                type="integer",
                help="Lower is offered first (default 100).",
                cli_help="",
                default=DEFAULT_PRIORITY,
            ),
            Param(
                "line",
                help="Release line this lands on (a name from [flow.lines], or the current line). Omit for the current line; tasks inherit a phase's line.",
                cli_help="release line (default: the current one)",
                default="",
            ),
            Param(
                "readd",
                type="boolean",
                help="File a REMOVED item's id again with this definition. An id still in the queue is always refused -- change that item with ddflow_update.",
                cli_help="file a REMOVED item's id again. An id still in the queue is always refused: change it with `ddflow update`",
            ),
            *ANSWER_PARAMS,
            Param(
                "phase",
                help="Owning phase id. Give this OR `parent`.",
                cli_help="owning phase",
                default="",
            ),
            Param(
                "parent",
                help="Owning phase id, OR another TASK's id, which makes this a SUB-TASK with its own globs and dependencies, run in parallel with its siblings. Same field as `phase` (the CLI has both names).",
                cli_help="owning phase OR task — a task parent makes this a SUB-TASK, which carries its own globs and dependencies like any other task",
                default="",
            ),
            Param(
                "lines",
                help="Release lines a FIX must reach, e.g. '1,2,3': written where [flow].port_strategy says, plus a port task `<id>@<line>` per other line.",
                cli_help="a FIX for several release lines, e.g. 1,2,3: written where [flow].port_strategy says, with a port task <id>@<line> generated for each other line",
                default="",
            ),
            Param(
                "port_of",
                help="Earlier fix this follows up: reuses the lines it reached.",
                cli_help="a FOLLOW-UP to an earlier fix: takes the lines that fix reached, so its own ports carry what this one lands",
                default="",
            ),
        ),
        tool_order=(
            "id",
            "phase",
            "parent",
            "title",
            "needs",
            "globs",
            "body",
            "tags",
            "priority",
            "line",
            "lines",
            "port_of",
            "readd",
            "relation",
            "check_only",
        ),
    ),
    Command(
        path=("split",),
        summary="split an item into sub-tasks in place, keeping its id and history",
        tool="ddflow_split",
        description="Split an item into sub-tasks IN PLACE when the work turns out to be two things -- the moment you discover it; mid-task discovery is normal. The original keeps its id and history and becomes an umbrella that completes when its children do; closing it and opening two new ones would lose the thread between what was planned and what happened. Children inherit the parent's globs: give each its own afterwards if they write different files, or they cannot run in parallel.",
        call=lambda repo, a, agent: _api().split(
            repo,
            a["id"],
            # The MCP argument is ONE comma-separated string; the CLI takes repeated
            # `--into`, a list. Split the string here rather than in the api, so the api
            # keeps the shape that cannot lose a spec containing a comma in its title.
            into=(
                list(a["into"])
                if isinstance(a.get("into"), list)
                else [x.strip() for x in str(a.get("into", "")).split(",") if x.strip()]
            ),
            globs=a.get("globs", "") or "",
            needs=a.get("needs", "") or "",
            agent=agent,
        ),
        payload=("item", "created"),
        render=L.split_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", help="The item to split.", cli_help="", positional=True),
            Param(
                "into",
                help="Comma-separated 'sub-id=title' pairs. At least two.",
                cli_help="repeatable: 'sub-id=title', or just 'sub-id'",
                tool_required=True,
                action="append",
                default=[],
            ),
            Param(
                "globs",
                help="Globs for the children (default: inherit).",
                cli_help="globs for the children (default: inherit the parent's); " + GLOBS_HELP,
                action=_Globs,
                default="",
            ),
            Param(
                "needs",
                help="Dependencies for the FIRST child. The others chain from it if you set theirs with ddflow_update.",
                cli_help="dependencies for the FIRST child",
                default="",
            ),
        ),
    ),
    Command(
        path=("resolve",),
        summary="settle a CONTESTED item (rival adds or claims from two clones), on the record",
        tool="ddflow_resolve",
        description="Settle a CONTESTED item: two clones each added the same id with different content, or each claimed it, and a merge brought both in (`ddflow_doctor` names them, `ddflow_show` lists the rivals, `ddflow_next` withholds them). `keep` names the definition (event id or agent) and/or the lease holder to keep; the losing claim is released in the same transaction; a losing DEFINITION comes back in `lost`: re-add it under a new id with `refile_as`, or it stays only in the log. Refused (exit 3) when not contested.",
        call=lambda repo, a, agent: _api().resolve(
            repo, a["id"], keep=a["keep"], refile_as=a.get("refile_as", "") or "", agent=agent
        ),
        payload=("id", "kept_definition", "kept_holder", "lost", "refiled", "released"),
        render=L.resolved_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", help="The contested item.", cli_help="", positional=True),
            Param(
                "keep",
                help="Event id (or a 6+ character prefix), agent, or lease holder to keep.",
                cli_help="the definition's event id (or a 6+ character prefix), or the agent / lease holder, to keep — `ddflow show <id>` lists them",
                required=True,
            ),
            Param(
                "refile_as",
                help="Comma-separated new ids, one per definition NOT kept, in `show` order: each is re-added under its new id in the same transaction.",
                cli_help="re-add each definition NOT kept under these new ids (comma-separated, in `show` order) in the same transaction",
                default="",
            ),
        ),
    ),
    Command(
        path=("update",),
        summary="change an item's fields",
        tool="ddflow_update",
        description="Change an item's fields. MOST IMPORTANT USE: widening `globs` when your "
        "work turns out to touch files outside what you claimed. Do that BEFORE "
        "writing them: the conflict detector and commit hook work from the declared "
        "globs, so an undeclared file is unprotected and the commit is refused.",
        # Typed, and the argv lambda that used to sit here is GONE rather than kept
        # "in case". The `api` branch runs first, so it was unreachable -- a second
        # encoding of the same operation that no test could have caught drifting,
        # because nothing executed it. That is the duplicate-then-drift shape, and
        # keeping a dead fallback is how it starts.
        #
        # `None` means leave alone and `[]` means clear, which is what
        # `_opt(clearable=True)` existed to rebuild after argv flattened both to an
        # empty string. Here the distinction is simply the values themselves.
        call=lambda repo, a, agent: _api().update(
            repo,
            a["id"],
            _api().ItemEdit(
                title=a.get("title"),
                body=a.get("body"),
                needs=_list_or_none(a, "needs"),
                # Raw, for the api's single read: split on commas here first, a JSON
                # array (or a glob holding a comma inside one) was lost (roborev).
                globs=(
                    None
                    if a.get("globs") is None
                    else list(a["globs"])
                    if isinstance(a["globs"], list)
                    else [a["globs"]]
                ),
                tags=_list_or_none(a, "tags"),
                priority=a.get("priority"),
                line=a.get("line"),
                resources=_list_or_none(a, "resources"),
                worktree=a.get("worktree"),
            ),
            agent=agent,
        ),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("title", help="New title.", cli_help=""),
            Param("body", help="New detail / acceptance criteria.", cli_help=""),
            Param(
                "needs",
                help="Comma-separated ids it depends on. Pass an EMPTY string to clear them — that is how you break a dependency cycle the loop detector found.",
                cli_help="",
            ),
            Param("tags", help="Comma-separated tags.", cli_help=""),
            Param("line", help="Move it to another release line.", cli_help=""),
            Param(
                "resources",
                help="Physical resources the work RUNS on, beside its files: 'gpu:4,vllm-fleet'. `next` withholds and `claim` refuses while live claims use up the capacity ([schedule] resources). Declare for a GPU job, model server or long run. Empty clears.",
                cli_help="",
            ),
            Param(
                "globs",
                help="Path globs this item writes, comma-separated or a JSON array. REPLACES the list (a claimed item's lease too); the result names what it dropped.",
                cli_help="REPLACES the item's globs (and a claimed item's lease) with these; "
                + GLOBS_HELP,
                action=_Globs,
            ),
            Param(
                "priority",
                type="integer",
                help="Lower is offered first (default 100).",
                cli_help="",
            ),
            Param(
                "worktree",
                help="Rebind the item and your live lease to this linked worktree (absolute, or repo-relative) and its branch: the way out of a wrong-tree binding, since re-claiming keeps the recorded tree and merge lands that tree's branch.",
                cli_help="rebind the item (and your live lease on it) to this linked worktree and the branch checked out there -- the way out of a binding to the wrong tree",
            ),
        ),
        tool_order=(
            "id",
            "globs",
            "needs",
            "title",
            "body",
            "tags",
            "priority",
            "line",
            "resources",
            "worktree",
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
