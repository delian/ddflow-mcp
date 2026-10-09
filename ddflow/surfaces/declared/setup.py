"""The setup commands, declared once: config, adopt, import and doctor, with the tools of
configure, setup, upgrade and import_verify.

`surfaces/parsers/` registers their command-line halves and `surfaces/tools/` takes their MCP
entries (D-unify 4, B-uni-cmd-migrate.6-rest). `config` and `adopt` are served by tools of other
names (`ddflow_configure`, `ddflow_setup`); `upgrade`'s command line stays hand-written (an
optional value on a flag, a `--confirm` list, a `--restore` that stands alone), its tool is
declared here.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _AGENT_KEYS, _api, _configure_reported
from ..vocabulary import sources

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("config",),
        reason="covered by ddflow_configure, which reads and writes the same knobs",
        via=("ddflow_configure",),
        summary="print every knob, its value and its source",
        params=(
            Param("explain", type="boolean"),
            Param("filter", default=""),
            Param(
                "set",
                help="edit one key in place, e.g. --set gate.unit_tests.command 'pytest -q'",
                default="",
            ),
            Param(
                "value",
                help="the value, when --set is used; or `KEY VALUE` with no --set (ddflow config review.max_rounds 0 --local)",
                positional=True,
                nargs="*",
                default=[],
            ),
            Param(
                "append_toml",
                help="append this TOML to .ddflow/config.toml (validated first)",
                default="",
            ),
            Param(
                "local",
                type="boolean",
                help="write --set/--append-toml to the git-ignored .ddflow/local/config.toml: this machine's endpoints, hosts, key variables and sizing, never committed",
            ),
        ),
    ),
    Command(
        path=(),
        tool="ddflow_configure",
        prose_reason="prints every knob with its documentation and its source",
        description='Read or write .ddflow/config.toml (WRITES). No arguments: every knob with value, source and meaning. `set` edits one dotted key in place (preferred); `toml` APPENDS a fragment, e.g.\n[gate.unit_tests]\ncommand = "pytest -q -n auto"\n(needs pytest-xdist). The committed file is generic project policy; anything of THIS machine or operator (a reviewer endpoint, a host, a key variable, worker counts) goes with local=true to the git-ignored .ddflow/local/config.toml, read last and never committed. A reviewer there is a `[[reviewer]]` block; `ddflow_reviewers_detect` write=true writes one.',
        kind="config",
        prose=True,
        call=_configure_reported,
        # PROSE, and `explain=True`: the string path was `config --explain`, which is
        # every knob with its documentation AND its source. The source is the half an
        # operator debugging a setting cannot do without.
        payload="text",
        params=(
            Param(
                "set",
                help="Dotted key to set, e.g. 'gate.unit_tests.command'. Preferred: it edits in place and works whether or not the section exists.",
                mcp_only=True,
            ),
            Param("value", help="The value for `set`.", mcp_only=True),
            Param(
                "toml",
                help="A whole TOML block to append. Fails if it would duplicate an existing table — use `set` instead then.",
                mcp_only=True,
            ),
            Param("filter", help="Only show knobs whose name contains this.", mcp_only=True),
            Param(
                "local",
                type="boolean",
                help="Write `set`/`toml` to the git-ignored .ddflow/local/config.toml instead of the committed config: for this machine's endpoints, hosts, key variables and sizing.",
                mcp_only=True,
            ),
        ),
    ),
    Command(
        path=("adopt",),
        reason="covered by ddflow_setup",
        via=("ddflow_setup",),
        summary="install ddflow into this project for one or more agents",
        params=(
            Param(
                "agents",  # GENERATED from the registry, never typed. A hand-kept list here drifted the moment
                # `cursor` was added, and `test_every_supported_agent_is_named_where_a_user_would_look`
                # exists because of it: a capability nobody can find is one nobody uses.
                help=f"comma-separated: {','.join(_AGENT_KEYS())} (default: all)",
                default="",
            ),
            Param("docs", help="where to write the drivers", default="docs/ddflow"),
            Param(
                "launch",
                help="how agents spawn the MCP server: 'auto' prefers uvx for an install from a package index and this installation otherwise; 'docker' needs no Python toolchain at all",
                default="auto",
                choices=("auto", "uvx", "docker", "python"),
            ),
            Param(
                "image",
                help="container image used by --launch docker",
                default="ghcr.io/OWNER/ddflow:latest",
            ),
            Param(
                "refresh_docs",
                type="boolean",
                help="rewrite ONLY the driver docs, the AGENTS.md/CLAUDE.md blocks and the agents' native rules from this ddflow's templates; leaves MCP launches, hooks and command files alone (what `doctor` points to when drivers lag)",
            ),
        ),
    ),
    Command(
        path=(),
        tool="ddflow_setup",
        prose_reason="a checklist of what it wrote and what to do next",
        description="Install ddflow into this repository: creates .ddflow/, writes the driver "
        "and the AGENTS.md section, and registers nothing else. Run this ONCE per "
        "project, then set your test command with ddflow_configure. Safe to re-run "
        "— it updates a managed block and leaves your own prose alone.",
        kind="setup",
        prose=True,
        call=lambda repo, a, agent, called_from=None: _api().adopt_project(
            repo,
            _api().Adoption(
                agents=a.get("agents", "") or "", refresh_docs=bool(a.get("refresh_docs"))
            ),
            agent=agent,
            called_from=called_from,
        ),
        # PROSE: a checklist of what it wrote and what to do next.
        payload="text",
        # Where the server stands decides where the committed files go: a linked
        # worktree's own checkout, not the shared primary (bug B1e7ad10c6c). A foreign
        # `as_agent` is not standing in the connection's tree (B11e4c5a185), so it is
        # asked from the primary, as from a CLI run there -- never the parent's branch.
        wants_called_from=True,
        params=(
            Param(
                "agents",  # GENERATED from the registry. Hand-kept copies of this list have drifted twice; an agent
                # reads this spec to decide what it may pass.
                help="Comma-separated agents to write driver deltas for: "
                f"{','.join(_AGENT_KEYS())}. Default: all.",
                mcp_only=True,
            ),
            Param(
                "refresh_docs",
                type="boolean",
                help="Rewrite ONLY the driver docs, AGENTS.md/CLAUDE.md blocks and adopted agents' native rules from this ddflow's templates (no MCP, hook or command-file changes), e.g. when ddflow_doctor notes drift. Refused on a never-adopted project.",
                mcp_only=True,
            ),
        ),
    ),
    Command(
        path=(),
        tool="ddflow_upgrade",
        description="What upgrading this project to the running ddflow would change, by category (repairs, migrations, config, instructions, hooks, mcp, features); an operator-set value needs their confirmation. Writes nothing unless `apply` is given (`plan` false = apply all): then it applies the plan after saving originals (.ddflow/backups or a git `snapshot`), returns the plan left and an `applied` report; exit 3 while an item needs `confirm`. `restore` undoes an apply. Plan exit: 0 up to date, 1 items.",
        call=lambda repo, a, agent: _api().upgrade(
            repo,
            plan=a.get("plan") if isinstance(a.get("plan"), bool) else None,
            apply=str(a.get("apply") or ""),
            confirm=[str(x) for x in (a.get("confirm") or [])],
            reason=str(a.get("reason") or ""),
            backup=str(a.get("backup") or ""),
            snapshot=bool(a.get("snapshot")),
            restore=str(a.get("restore") or ""),
            agent=agent,
        ),
        # The plan's fields; an apply also carries what it did (`applied`) and a restore what
        # it put back (`restored`), so the body is the same parsed value as the CLI's `--json`.
        payload=lambda a: (
            _api().setup.UPGRADE_RESTORE_PAYLOAD
            if a.get("restore")
            else _api().setup.UPGRADE_APPLY_PAYLOAD
            if (a.get("apply") or a.get("plan") is False)
            else _api().setup.UPGRADE_PAYLOAD
        ),
        params=(
            Param(
                "plan", type="boolean", help="Dry run (default); false applies all.", mcp_only=True
            ),
            Param("apply", help="Categories to apply: all, or a comma list.", mcp_only=True),
            Param(
                "confirm",
                type="array",
                help="With apply: keys the operator accepts (needs reason).",
                mcp_only=True,
            ),
            Param("reason", help="Why they accept it.", mcp_only=True),
            Param("backup", help="local, snapshot or none.", mcp_only=True),
            Param("snapshot", type="boolean", help="apply: git snapshot backup.", mcp_only=True),
            Param("restore", help="Undo: backup name or latest; alone.", mcp_only=True),
        ),
    ),
    Command(
        path=("import",),
        summary="propose the existing project's work, lessons and decisions (exit 2 = nothing)",
        tool="ddflow_import",
        flag_exempt={"--verify": "covered by ddflow_import_verify, its own tool"},
        description="For a project that ALREADY HAS HISTORY and is adopting ddflow now: reads its todo checklists, lessons corpus, ADR files and unmerged branches and proposes them as queue items. Reports by default; writes NOTHING until `apply` is true. Call it right after `ddflow_setup` on any repository that is not brand new. The proposal is a GUESS: the `import-existing-project` prompt walks through fixing it. Exit 2: nothing found.",
        call=lambda repo, a, agent: _api().import_project(
            repo,
            apply=bool(a.get("apply")),
            include_done=bool(a.get("include_done")),
            max_tasks=int(a.get("max_tasks") or 0),
            agent=agent,
        ),
        payload="",
        params=(
            Param(
                "apply",
                type="boolean",
                help="Write the proposal. Default false: look first.",
                cli_help="write them; default is a dry run",
            ),
            Param(
                "verify",
                type="boolean",
                help="report what was already imported and whether it is still true: source drift, vanished source files, and the globs and decisions the import left for a human (exit 1 = findings, 2 = nothing imported)",
                cli_only=True,
            ),
            Param(
                "include_done",
                type="boolean",
                help="Also import already-ticked items as completed. Off by default — a finished history is not a queue, and one real project yielded 3,638 of them.",
                cli_help="also import already-ticked items, as completed. Off by default: a finished history is not a queue.",
            ),
            Param(
                "max_tasks",
                type="integer",
                help="Refuse to propose more tasks than this (default 200).",
                cli_help="refuse to propose more tasks than this. 0 (the default) uses [importer] max_tasks from the config, which ships at 200. A bigger number is usually a whole history rather than a queue.",
                default=0,
            ),
        ),
    ),
    Command(
        path=(),
        tool="ddflow_import_verify",
        description="Was this project's history imported, is that still true, and did anyone FINISH it? Read-only. "
        "STATUS: what carries import provenance. STILL TRUE: whether the sources moved on (what a "
        "re-run would add) or an imported item names a missing file. FINISHED: imported tasks with "
        "no globs (the conflict detector cannot protect them) and phases claiming shipped work "
        "while a task under them is open. Call after any import. Exit 1 = findings; exit 2 = "
        "nothing ever imported. `ddflow_doctor` covers the rest.",
        call=lambda repo, a, agent: _api().import_verify(repo, agent=agent),
        payload="",
        params=(),
    ),
    Command(
        path=("doctor",),
        summary="integrity + health check",
        tool="ddflow_doctor",
        flag_exempt={"--upgrade": "the same as `ddflow upgrade`, which is `ddflow_upgrade`"},
        prose_reason="a health report written to be read, with remedies in prose",
        description="Integrity and health check: log corruption, dependency cycles, "
        "unknown dependencies, orphaned worktrees, stale index.",
        kind="doctor",
        prose=True,
        call=lambda repo, a, agent: _api().doctor(
            repo, agent=agent, parser=sources()[0], tools=sources()[1]
        ),
        # PROSE, as it has always been: a list of problems with advice attached is
        # what an operator and an agent both want, and `views/human.py` renders it once
        # for both.
        payload="text",
        params=(
            Param(
                "upgrade",
                type="boolean",
                help="the upgrade plan instead (the same as `ddflow upgrade`)",
                cli_only=True,
            ),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
