"""The loop's find-and-lease commands: next, brief, claim, heartbeat, release, wait."""

from __future__ import annotations

from shlex import quote

from ....views.output_caps import from_args as _cap
from ..capflags import capped
from ._shared import (
    AB,
    DEFAULT_CHECK_RECOVERY,
    DEFAULT_NEXT_KIND,
    DEFAULT_WAIT_TIMEOUT_S,
    GLOBS_HELP,
    MCP_WAIT_DEFAULT_S,
    MCP_WAIT_MAX_S,
    NOTHING,
    OK,
    OVERRIDE_EXEMPT,
    CliPolicy,
    Command,
    L,
    Param,
    _api,
    _Globs,
    _wait_timeout,
)


def _next(repo, a, agent):
    """`next`, or with `--lane` the pull answer for that lane."""
    kind, phase = a.get("kind") or _api().DEFAULT_NEXT_KIND, a.get("phase", "") or ""
    if lane := a.get("lane", ""):
        out = _api().next_lane(repo, lane=lane, kind=kind, phase=phase, agent=agent)
        return _cap(repo, out, f"next --lane {lane}", a)  # its footer names the lane call
    return _cap(repo, _api().next_item(repo, kind=kind, phase=phase, agent=agent), "next", a)


def _next_notes(out, a, ctx):
    """A pull answer says everything in its text; the queue's notes read its plan."""
    return () if a.get("lane") else L.next_notes(out, a, ctx)


def _next_text(out, a, ctx):
    if not a.get("lane"):
        return L.next_text(out, a, ctx)
    if out.exit == OK:
        d = out.data
        return f"{d['lane']}: take {d['item']}\n  ddflow claim {quote(d['item'])} --note {quote(d['note'])}"
    return f"{a['lane']}: {out.reason}"


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("next",),
        summary="what may start now (exit 2 = nothing actionable, exit 1 = unknown --phase)",
        tool="ddflow_next",
        description="What may be started RIGHT NOW, and why the rest may not. Independent "
        "ready items can run in parallel worktrees. ready=[] is a result, not an error: "
        "it never means 'pick something anyway'.",
        call=_next,
        payload="",
        cli=CliPolicy(notes=_next_notes, human=_next_text),
        params=(
            Param(
                "phase",
                help="Restrict to one phase (the 'implement phase X' entry point).",
                cli_help="limit to this phase; exit 1 if it names no phase or item",
                default="",
            ),
            Param(
                "kind",
                help="'task' (default) or 'phase'.",
                cli_help="",
                default=DEFAULT_NEXT_KIND,
                choices=("task", "phase"),
            ),
            Param(
                "lane",
                help="Answer this idle lane.",
                cli_help="answer this idle lane: its best item and a note for claim",
                default="",
            ),
            *capped(True),
        ),
    ),
    Command(
        path=("brief",),
        summary="budgeted session-start pack",
        tool="ddflow_brief",
        description=(
            "START HERE every session. Returns a budgeted pack: work recoverable after "
            "a crash, the current item, what is ready to start now, why everything else "
            "is blocked, and the past lessons ranked as relevant to this task. Use this "
            "INSTEAD of reading the project's lesson or rule files: the same information, "
            "ranked for the task at hand, at a fraction of the tokens."
        ),
        kind="brief",
        prose=True,
        prose_reason="a budgeted reading pack — rules, decisions and lessons as text to read",
        call=AB.call,
        payload="text",
        cli=CliPolicy(
            notes=L.brief_notes,
            body=AB.brief_body,
            human=L.brief_text,
            call_kwargs=L.merge_kwargs,
        ),
        wants_called_from=True,
        flag_exempt={
            "--note": "with --for-agent, a coordinator's note: CLI only (tools/list bytes)"
        },
        params=(
            Param(
                "item", help="Focus on this phase or task id (optional).", cli_help="", default=""
            ),
            Param(
                "phase",
                help="Restrict the ready set to this phase (optional).",
                cli_help="",
                default="",
            ),
            Param(
                "check_recovery",
                type="boolean",
                help="Scan for crashed agents' worktrees and lead with them: know it BEFORE new work.",
                cli_help="",
                default=DEFAULT_CHECK_RECOVERY,
            ),
            Param(
                "for_agent",
                help="Render this subagent's launch brief for item.",
                cli_help="render the standard launch brief of subagent NAME for the item "
                "(`--item` or the positional id)",
                default="",
            ),
            Param(
                "note",
                help="",
                cli_help="with --for-agent: the coordinator's note for this item",
                default="",
                cli_only=True,
            ),
            Param(
                "target",
                help="",
                cli_help="the item id (as --item; required with --for-agent)",
                positional=True,
                nargs="?",
                default="",
                cli_only=True,
            ),
        ),
    ),
    Command(
        path=("claim",),
        summary="lease an item + create its worktree (exit 3 = refused)",
        tool="ddflow_claim",
        description="Lease an item and create its isolated git worktree. Refuses (exit 3) if "
        "another agent holds it or an overlapping item, and names what you could take "
        "instead. NEVER steals an expired lease: a crashed agent's tree often holds "
        "finished work.",
        call=L.claim_via_bug,
        # `rebound`/`here`: the item kept its OWN tree from an earlier claim, and whether
        # the caller is standing in it -- over MCP the payload is the only way an agent
        # learns it has to move there.
        payload=(
            "item",
            "holder",
            "worktree",
            "branch",
            "base",
            "rebound",
            "here",
            "tree",
            "port",
            "port_advice",
            "broke",
            "bug",
        ),
        # `claim` is the one operation that needs to know WHERE THE CALLER IS, not just
        # which repo: adoption turns on whether the caller was already standing in a
        # worktree. The dispatcher passes it only to tools that ask.
        cli=CliPolicy(shown=(OK,), human=L.claim_text, body=L.claim_body),
        wants_called_from=True,
        params=(
            Param(
                "id",
                help="Item id to claim, or an open bug id (its fix task is claimed, filed first if none).",
                cli_help="item id, or an open bug id: its fix task is claimed (filed first if it has none)",
                positional=True,
            ),
            Param(
                "globs",
                help="Comma-separated path globs this work will write.",
                cli_help="what this claim writes (recorded on the item too); " + GLOBS_HELP,
                action=_Globs,
            ),
            Param("note", help="What you intend to do.", cli_help=""),
            Param(
                "force",
                type="boolean",
                help="Override a refusal -- a person's route; an agent's `force` is dropped, so an agent files `approval=request` instead. A forced claim past a dependency or live lease lets two agents write one file. Recorded.",
                cli_help="",
            ),
            Param(
                "no_worktree",
                type="boolean",
                help="Lease the item without creating a worktree. For work that is not a code change — a research or review task.",
                cli_help="",
            ),
            Param(
                "resources",
                help="Physical resources this claim holds, e.g. 'gpu:2'; they REPLACE the item's declared ones. Refused (exit 3) when live claims already use the capacity ([schedule] resources), every holder counted.",
                cli_help="the resources this claim reserves, e.g. 'gpu:2' (recorded on the item too)",
                default="",
            ),
            Param(
                "queue",
                type="boolean",
                help="Queue a refused request (FIFO per file).",
                cli_help="keep a refused claim in the first-in-first-out line for its files",
            ),
            Param(
                "break",
                type="boolean",
                help="Break a contended, idle lease (work kept).",
                cli_help="break a contended lease idle for [lease].hold_max_s (its work is kept)",
            ),
            Param(
                "approval",
                help="An agent's way past a refusal --force would override: 'request' asks the operator, then the approval id they answered.",
                cli_help="an agent: 'request' files an approval request for taking the item over a refusal --force would override (the operator answers in chat), then the approval id",
                default="",
            ),
        ),
        tool_order=(
            "id",
            "globs",
            "note",
            "no_worktree",
            "resources",
            "queue",
            "break",
            "force",
            "approval",
        ),
    ),
    Command(
        path=("heartbeat",),
        summary="renew a lease",
        tool="ddflow_heartbeat",
        description="Renew the lease on an item. Call periodically during long work, "
        "or the lease expires and another agent may take the item.",
        call=L.heartbeat_call,
        payload=("renewed", "waiters", "globs_withheld", "yield_requested", "recall", "wake"),
        render=L.heartbeat_text,
        cli=CliPolicy(
            notes=L.heartbeat_notes,
            body=lambda out, a: out.body(
                ("renewed", "waiters", "globs_withheld", "new_reports", "recall")
            ),
            call_kwargs=L.heartbeat_kwargs,
        ),
        # The item's own tree renews its lease whoever claimed it -- the CLI may resolve a
        # DIFFERENT identity than the holder (a harness declaration, `as_agent`), so the
        # tree check must answer from where the caller actually stands. `heartbeat_kwargs`
        # passes that raw start past the owner-check redirect (`Ctx.called_from`), which
        # answered "no lease held" from exactly the tree `claim` made (Bad4752f936).
        wants_called_from=True,
        params=(
            Param(
                "id",
                help="Item id.",
                cli_help="",
                positional=True,
                nargs="?",
                default="",
            ),
        ),
    ),
    Command(
        path=("release",),
        summary="give up a lease",
        tool="ddflow_release",
        description="Give up a lease without completing the item — when you are handing off, "
        "stopping, or recovering someone else's abandoned work after inspecting it. "
        "The note is recorded in the log and is often the only lasting explanation "
        "of why a claim was broken.",
        call=lambda repo, a, agent: _api().release_item(
            repo,
            a["id"],
            note=a.get("note", "") or "",
            agent=agent,
            force=bool(a.get("force")),
            reason=a.get("reason", "") or "",
        ),
        payload=("released", "woke", "wake"),
        flag_exempt=OVERRIDE_EXEMPT,
        render=L.release_text,
        # A refusal (another clone's live lease) says why on stderr, not "no lease on".
        cli=CliPolicy(shown=(OK, NOTHING)),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("note", help="Why you are releasing it.", cli_help=""),
            Param("reason", cli_only=True, cli_help="why --force is needed (recorded)", default=""),
            Param(
                "force",
                type="boolean",
                cli_only=True,
                cli_help="override ANOTHER CLONE's live lease on the item (needs --reason; recorded on the event)",
            ),
        ),
    ),
    Command(
        path=("wait",),
        summary="sleep until an item (or anything) can be claimed; exit 2 = deadline, or waiting cannot help",
        tool="ddflow_wait",
        flag_exempt={
            "--max-s": "a hard cap for a shell caller; over MCP the call is already bounded by "
            "`timeout` (at most MCP_WAIT_MAX_S) and the client re-asks",
        },
        description="Sleep until an item can be claimed (or, with no item, until anything is ready) and return the moment it can. Use it instead of polling or asking the operator when a claim was refused for another agent's lease or overlapping files, or an unfinished dependency someone is working on. Exit 0: claim now (it says what freed it). Exit 2: deadline passed, or waiting cannot help (done, cycle, operator hold, a dependency nobody works on) and it says what to do. The holder is told you wait. With no item and no lease it returns the first ready item AT ONCE, whatever it is: to wait for a held file or an agent's leases give `lease`.",
        call=lambda repo, a, agent: _api().wait_item(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            globs=a.get("globs") or None,
            lease=a.get("lease", "") or "",
            token=a.get("token", "") or "",
            approval=a.get("approval", "") or "",
            timeout_s=_wait_timeout(a, repo),
            poll_s=a.get("poll"),
            max_s=a.get("max_s"),
            agent=agent,
        ),
        payload="",
        params=(
            Param(
                "item",
                help="The item to wait for (default: anything ready).",
                cli_help="the item to wait for (default: anything ready)",
                default="",
            ),
            Param(
                "phase",
                help="With no item: anything ready in this phase.",
                cli_help="with no --item: anything ready in this phase",
                default="",
            ),
            Param(
                "kind",
                help="'task' (default) or 'phase'.",
                cli_help="",
                default=DEFAULT_NEXT_KIND,
                choices=("task", "phase"),
            ),
            Param(
                "globs",
                help="With item: the globs you will claim with (comma-separated or a JSON array), so ready means that claim will not be refused for them.",
                cli_help="with --item: the globs you will claim with, so READY means that claim will succeed; "
                + GLOBS_HELP,
                action=_Globs,
            ),
            Param(
                "lease",
                help="Wait for a LEASE instead of an item: an agent id (until it holds nothing) or a path/glob (until no other agent's live lease covers it). A bare wait returns the first ready item at once.",
                cli_help="wait for a lease instead of an item: an agent id (until it holds nothing) or a "
                "path/glob (until no other agent's live lease covers it); a bare wait returns the first "
                "ready item at once",
                default="",
            ),
            Param(
                "approval",
                help="Wait for an APPROVAL instead of an item or lease: an operator-approval request id, until the person answers it. Ready then means re-run the gated command with --approval ID.",
                cli_help="wait for an operator-approval request id until it is answered; ready then means "
                "re-run the gated command with --approval ID",
                default="",
            ),
            Param(
                "token",
                help="The resume token a bounded wait returned (`wake.token`): asks the same question again; item, phase, lease and globs given with it win.",
                cli_help="resume token from an earlier wait",
                default="",
            ),
            Param(
                "timeout",
                type="number",
                help=f"Seconds to wait (default {MCP_WAIT_DEFAULT_S}, at most {MCP_WAIT_MAX_S}: "
                "a client may time a tool call out, so call again to keep waiting). 0 asks "
                "without waiting.",
                cli_help=f"seconds to wait (default {DEFAULT_WAIT_TIMEOUT_S}; 0 asks without waiting)",
            ),
            Param(
                "poll",
                type="number",
                help="Seconds between log checks (default 2).",
                cli_help="seconds between log checks",
            ),
            Param(
                "max_s",
                type="number",
                cli_only=True,
                cli_help="never wait longer than this many seconds: exit 2 naming the live holder "
                "and how long it has held (a holder that keeps renewing is not a reason to wait on)",
            ),
        ),
    ),
)
