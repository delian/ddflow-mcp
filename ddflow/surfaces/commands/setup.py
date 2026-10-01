"""`companions`, `config`, `prompts`, `hooks`, `help`, `adopt` — the human surface for
`api.setup`.

`cmd_companions`' renderer is the substantial one, and it is presentation all the way
down: a mark per state, the right remedy per state, and a closing block addressed to the
AGENT because the agent is who reads it. ddflow installs nothing itself — running an
install command on somebody's machine is the operator's decision — but "here is a gap"
without "here is what to do about it" is a report nobody acts on.
"""

from __future__ import annotations

import json
import sys

from ...api import setup as A
from ...infra import worktree as W
from ..context import FAIL, NOTHING, OK, Ctx, _wrap

_MARKS = {"registered": "[x]", "installed": "[+]", "missing": "[ ]", "unknown": "[?]"}


def _companion_lines(statuses) -> list[str]:
    """One block per companion: what state it is in, and the remedy for that state."""
    lines: list[str] = []
    for st in statuses:
        mark = _MARKS[st.state]
        tag = "" if st.companion.default else "  (opt-in)"
        if not st.companion.is_mcp and st.installed is True:
            mark = "[x]"
        lines.append(f"  {mark} {st.companion.id:<10s} {st.companion.title}{tag}")
        lines.append(f"       gates: {', '.join(st.companion.gates) or '—'}")
        if st.state == "registered":
            # Named when it differs from the id: an operator looking for `codeguide` in
            # `.mcp.json` finds `coding-guides`, and the report must say that is the one.
            lines.append(
                "       registered for: "
                + ", ".join(
                    a
                    if st.registered_as.get(a, st.companion.id) == st.companion.id
                    else f"{a} (as `{st.registered_as[a]}`)"
                    for a in st.registered_in
                )
            )
        elif st.state == "installed" and not st.companion.is_mcp:
            lines.append(
                f"       installed ({st.detail}). A {st.companion.kind} tool — the agent "
                f"shells out to it, so there is nothing to register."
            )
        elif st.state == "installed":
            lines.append(f"       installed ({st.detail}) but no agent is configured to launch it.")
            lines.append(f"       -> ddflow companions add --id {st.companion.id}")
        elif st.state == "unknown":
            lines.append(f"       not checked ({st.detail}) — re-run without --no-probe")
        else:
            lines.append(f"       not here: {st.detail}")
            lines.append(f"       -> ask the operator, then: {st.companion.install}")
            if st.companion.note:
                lines.append(f"          note: {st.companion.note}")
            if st.companion.is_mcp:
                lines.append(f"          then: ddflow companions add --id {st.companion.id}")
            if st.companion.url:
                lines.append(f"          {st.companion.url}")
        if st.companion.why:
            lines.append(f"       {st.companion.why.strip().splitlines()[0]}")
        lines.append("")
    return lines


def _companions_list(a, c: Ctx) -> int:
    out = A.companions(
        c.repo, no_probe=bool(getattr(a, "no_probe", False)), agent=c.requested_agent
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(("companions", "gate_coverage", "uncovered_gates")), indent=2))
        return out.exit
    statuses = out.data["_render"]["statuses"]
    lines = ["Companion tools", "", *_companion_lines(statuses)]
    if out.data["uncovered_gates"]:
        lines += [
            "Gates in this project's task pipeline with no companion behind them:",
            f"  {', '.join(out.data['uncovered_gates'])}",
            "  Not a failure — several of these are judgement an agent does directly.",
            "  It is the list to check when a gate has been passing suspiciously easily.",
            "",
        ]
    if out.exit == NOTHING:
        # Addressed to the AGENT, because the agent is who reads this.
        lines += [
            "WHAT TO DO ABOUT THIS (agent):",
            "  Tell the operator which of these are missing, what each one buys, and",
            "  what installing it would run. If they agree, run the install command",
            "  yourself and then `ddflow companions add --id <id>`. If they decline,",
            "  record the gates it serves as `unavailable` when you reach them — never",
            "  as passed on your own word.",
            "",
        ]
    print("\n".join(lines))
    return out.exit


def _companions_add(a, c: Ctx) -> int:
    out = A.companions_add(
        c.repo,
        A.Registration(
            ids=a.id or "",
            agents=a.agents or "",
            force=bool(a.force),
            dry_run=bool(getattr(a, "dry_run", False)),
        ),
        agent=c.requested_agent,
    )
    if out.exit in (FAIL, NOTHING) and not out.data.get("actions"):
        print(out.reason, file=sys.stderr)
        return out.exit
    if out.exit not in (OK, FAIL):
        print(out.reason, file=sys.stderr)
        return out.exit
    head = (
        "Nothing was written. Show the operator this, and register it only if they agree:\n"
        if out.data["dry_run"]
        else ""
    )
    c.out(
        head + "\n".join(f"  {x}" for x in out.data["actions"]),
        out.body(("actions", "applied", "written", "refused")),
    )
    return out.exit


def cmd_companions(a, c: Ctx) -> int:
    """Which companion MCP servers serve this project's gates, and what is missing.

    Exit 0 when every default companion is registered; exit 2 on a gap — "no data"
    reported as itself, never collapsed into "no problem". Never exits 1 for a gap: a
    missing optional server is something to close, not a failure of this command.
    """
    if a.companions_cmd == "add":
        return _companions_add(a, c)
    return _companions_list(a, c)


def cmd_config(a, c: Ctx) -> int:
    out = A.configure(
        c.repo,
        A.ConfigEdit(
            set=a.set or "",
            value=getattr(a, "value", "") or "",
            append_toml=a.append_toml or "",
            filter=a.filter or "",
            explain=bool(a.explain),
            local=bool(getattr(a, "local", False)),
        ),
        agent=c.requested_agent,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if a.set:
        where = f"   (in {out.data['path']}, not committed)" if out.data.get("local") else ""
        c.out(
            f"{out.data['key']} = {out.data['literal']}{where}",
            out.body(("key", "value", "path", "local")),
        )
        return OK
    if a.append_toml:
        c.out(f"appended to {out.data['path']}", out.body(("path",)))
        return OK
    if c.json:
        print(json.dumps(out.body("rows"), indent=2, default=str))
        return OK
    for row in out.data["rows"]:
        print(f"{row['key']} = {row['value']!r}   [{row['source']}]")
        if a.explain and row["doc"]:
            for line in _wrap(row["doc"], 76):
                print(f"    {line}")
    return OK


def cmd_prompts(a, c: Ctx) -> int:
    out = A.prompts(
        c.repo,
        action=a.prompts_cmd or "list",
        name=getattr(a, "name", "") or "",
        force=bool(getattr(a, "force", False)),
        agent=c.requested_agent,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    action = out.data["action"]
    if action == "show":
        print(out.data["text"])
        return OK
    if action == "eject":
        for path in out.data["skipped"]:
            print(f"  skipped {path} (exists; --force to overwrite)")
        c.out(
            "\n".join(f"  wrote {w}" for w in out.data["written"])
            + "\n\nThese now take precedence over the shipped defaults. Edit them "
            "freely; they are plain text and are not parsed as code.",
            {"written": out.data["written"]},
        )
        return OK
    rows = out.data["rows"]
    if c.json:
        print(json.dumps(rows, indent=2))
        return OK
    # Grouped, because the two kinds are used for entirely different things: a template is
    # machinery (the review prompt, the gate instruction, the MCP handshake) and a command
    # is a workflow an operator invokes. A flat list of eleven names invites
    # `prompts show mcp_instructions` expecting a workflow.
    for title, kind in (("Templates", "template"), ("Workflow commands", "command")):
        members = [t for t in rows if t["kind"] == kind]
        if not members:
            continue
        print(f"{title}:")
        for t in members:
            print(f"  {t['name']:<24} [{t['source']:<7}] {t['path']}")
        print()
    print(
        "Edit any of them with `ddflow prompts eject <name>`, which copies the "
        "shipped default into .ddflow/prompts/ where it takes precedence.\n"
        "Read one with `ddflow prompts show <name>`."
    )
    return OK


def cmd_hooks(a, c: Ctx) -> int:
    out = A.hooks(
        c.repo,
        action=a.hooks_cmd or "status",
        force=bool(getattr(a, "force", False)),
        claude=bool(getattr(a, "claude", False)),
        msg_file=getattr(a, "msg_file", "") or "",
        agent=c.requested_agent,
    )
    if a.hooks_cmd == "session-start":
        # Claude Code puts this STDOUT into the session's context. Always exit 0: a hook
        # that fails at session start blocks nothing useful.
        print(out.data["message"])
        return OK
    if a.hooks_cmd in ("check-commit", "check-msg"):
        # The MESSAGE is the product here, and it goes to stderr because a commit hook's
        # output is diagnostics, not data.
        if out.data["message"]:
            print(out.data["message"], file=sys.stderr)
        return out.exit
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(out.data["message"], out.body(("installed", "policy", "session_hook", "trailer_hook")))
    return out.exit


def help_topics() -> list[str]:
    """The topic names, read from the one place that defines them.

    Imported lazily and inside a function so `build_parser` does not drag the MCP tool
    table in through `help.grouped_tools`: the parser is built on EVERY invocation,
    including `ddflow next` in a hot loop.
    """
    from ...services.help import TOPICS

    return list(TOPICS)


def cmd_help(a, c: Ctx) -> int:
    """`ddflow help [topic]` — what this is, what it can do, what the workflow is.

    Not argparse's `--help`, which lists 43 subcommands alphabetically and explains
    neither what any of them is for nor which to reach for first.
    """
    # The tool registry is handed IN: `services/` sits below `surfaces/`. Function-local
    # because the parser is rebuilt on every invocation and must not drag it along.
    from ..mcp import TOOLS

    out = A.help_topic(c.repo, topic=a.topic or "", tools=TOOLS, agent=c.requested_agent)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    c.out(out.data["text"], out.body(("topic", "text", "topics")))
    return OK


def cmd_adopt(a, c: Ctx) -> int:
    out = A.setup(
        c.repo,
        A.Adoption(
            agents=a.agents or "",
            docs=a.docs,
            launch=a.launch or "",
            image=a.image or "",
        ),
        agent=c.requested_agent,
    )
    if out.exit == FAIL and not out.data.get("actions"):
        print(out.reason, file=sys.stderr)
        return FAIL
    # A REFUSAL with actions is a partial adoption: everything else WAS written, so init
    # and the report still happen -- and then the exit code says it is not done.
    # `setup` already wrote the init files -- the same call `ddflow_setup` makes -- so
    # only the report is left. `_report_init` lives HERE, not in `cli`, because reaching
    # back up into the surface this module was extracted out of would recreate the
    # module-level cycle `test_no_mutually_importing_pair_has_a_module_level_edge` forbids.
    _report_init(c)
    c.out(
        out.data["text"],
        out.body(("actions", "agents", "companions_ready", "companions_absent")),
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    return OK


# -- parser ----------------------------------------------------------------------------


def cmd_init(a, c: Ctx) -> int:
    """`ddflow init`. The writes are `api.setup.init_project`'s; this only reports them."""
    A.init_project(c.repo, agent=c.requested_agent)
    return _report_init(c)


def _report_init(c: Ctx) -> int:
    """What `init` and `adopt` print after the init files exist: where, and what to commit.

    Presentation only. The files themselves are written below the surfaces
    (`services.adopt.init_files`), because a write that lives here is a write the MCP
    `ddflow_setup` never makes -- which is how bug B185ec008b4 happened.
    """
    d = c.repo / ".ddflow"
    cfgp = d / "config.toml"
    c.store.rebuild(c.log)
    # Setup touches TRACKED files (.gitignore, .gitattributes, AGENTS.md). Leaving them
    # uncommitted makes the primary checkout dirty, and `ddflow merge` then refuses --
    # correctly, but with a message about "modified tracked files" that gives no hint
    # the cause was ddflow's own setup two commands ago. Say so here instead.
    touched = [
        rel
        for rel in (
            ".gitignore",
            ".gitattributes",
            ".ddflow",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/ddflow",
        )
        if (c.repo / rel).exists() and W.git(c.repo, "status", "--porcelain", "--", rel).out.strip()
    ]
    commit_hint = ""
    if touched:
        commit_hint = (
            "\n\n  Setup changed these files — commit them before your first merge, or "
            "the primary\n  checkout stays dirty and `ddflow merge` will refuse:\n"
            f"    git add {' '.join(touched)} && git commit -m 'ddflow: adopt'"
        )
    # The rules surface, REPORTED not written. `init` creates `.ddflow/`; writing prose into
    # someone's AGENTS.md is `adopt`'s job and needs to be asked for, because the file is
    # theirs and usually has their own content in it. Reporting it here is what stops a
    # repo sitting "initialised" with an agent that has no project rules — which nothing
    # noticed before, since adoption is judged by the config file alone.
    from ...services.adopt import rules_status

    drift = [r for r in rules_status(c.repo) if r.needs_attention]
    rules_hint = ""
    if drift:
        rules_hint = (
            "\n\n  "
            + "\n  ".join(r.render() for r in drift)
            + (
                "\n  `ddflow adopt` writes it — only the block between the DDFLOW markers, "
                "leaving your own prose alone."
            )
        )
    c.out(
        f"Initialised ddflow in {d}\n"
        f"  config: {cfgp}\n"
        f"  Next: `ddflow phase add P1 --title 'First phase'`" + commit_hint + rules_hint,
        {"root": str(d), "config": str(cfgp), "rules_drift": [r.state for r in drift]},
    )
    return OK
