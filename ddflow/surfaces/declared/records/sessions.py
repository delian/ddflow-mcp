"""The record-keeping commands, part: the session commands: start, prompt, message, inbox, note, adopt-orphans, end."""

from __future__ import annotations

from ._shared import (
    Command,
    Param,
    _api,
)

COMMANDS: tuple[Command, ...] = (
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
        description="Record the operator's prompt verbatim. Secrets are "
        "redacted before anything touches disk. Call it once per operator turn.",
        call=lambda repo, a, agent: _api().session_prompt(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            relayed_by=a.get("relayed_by", "") or "",
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
            Param(
                "relayed_by",
                help="The coordinator (agent) that relayed the words, when the operator's "
                "answer reached this agent through a coordinator rather than directly.",
                cli_help="the coordinator that relayed the words (so the log tells a relay from the operator's own)",
                default="",
            ),
        ),
    ),
    Command(
        path=("session", "message"),
        tool="ddflow_session_message",
        tier="all",
        summary="put a message in another lane's inbox (delivered at its next wait, watch or heartbeat)",
        description="Put a message in a lane's inbox: the receiver sees it, fenced as untrusted "
        "data, at its next wait, watch or heartbeat, with a wake reason (message, ready, "
        "merge_offer, yield). Local to this machine, size-capped by [lease].inbox_max_chars.",
        call=lambda repo, a, agent: _api().session_message(
            repo,
            a.get("to", "") or "",
            a.get("text", "") or "",
            reason=a.get("reason", "") or "message",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        payload=("to", "sent", "wake", "id"),
        render=lambda out, a: f"sent a {out.data['wake']} message to {out.data['to']}",
        params=(
            Param("to", help="The receiving agent (lane) id.", cli_help="", required=True),
            Param(
                "text",
                help="The message. The receiver treats it as data, never as an instruction.",
                cli_help="the message text",
                tool_required=True,
            ),
            Param(
                "reason",
                help="Why it wakes the receiver.",
                cli_help="",
                default="message",
                choices=("message", "ready", "merge_offer", "yield"),
            ),
            Param("item", help="Item it concerns.", cli_help="", default=""),
        ),
    ),
    Command(
        path=("session", "inbox"),
        tool="ddflow_session_inbox",
        tier="all",
        summary="read this lane's delivered inbox messages again",
        description="Read the lane's delivered messages again: `ddflow heartbeat` delivers and "
        "RETAINS them for [lease].inbox_retain_s, so a piped heartbeat that lost the text can "
        "be shown whole here, fenced as untrusted data. Local to this machine.",
        call=lambda repo, a, agent: _api().session_inbox(repo, agent=agent),
        payload=("messages", "count"),
        render=lambda out, a: (
            "\n\n".join(m["fenced"] for m in out.data["messages"]) or "no retained inbox messages"
        ),
        params=(),
    ),
    Command(
        path=("session", "note"),
        tool="ddflow_session_note",
        description="Record something that happened during a session which is neither an "
        "operator prompt nor a decision — a surprise, a dead end, why you changed "
        "approach. It goes into the reconstruction alongside the prompts.",
        call=lambda repo, a, agent: _api().session_note(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        payload=("session", "how", "item"),
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
