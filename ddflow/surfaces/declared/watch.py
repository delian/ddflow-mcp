"""The `watch` command, declared once: wait for a job, a file line, a process or a gate while
the caller's leases are renewed (DX-watch-job).

`surfaces/parsers/by_convention.py` registers the command-line half; the tool is one action of
the `lease` family, beside `wait`.
"""

from __future__ import annotations

from ...core.defaults import DEFAULT_WAIT_TIMEOUT_S
from ..registry import Command, Param, by_tool
from ..tools._common import MCP_WAIT_DEFAULT_S, MCP_WAIT_MAX_S, _api, _wait_timeout


def _text(out, a) -> str:
    from .lifecycle_cli import wake_text  # the one rendering of a lane's fenced messages

    d = out.data
    msgs = (
        wake_text({"reason": d["wake"]["reason"], "messages": d["wake"]["messages"]})
        if (d.get("wake") or {}).get("messages")
        else ""
    )
    if out.exit:
        return (f"{out.reason}\ntoken {d['token']}" if "token" in d else out.reason) + msgs
    if d.get("matched") is False:  # woken by the inbox before the target happened
        return f"woken by {d['wake']['reason']}; still watching {d['target']}\ntoken {d['token']}{msgs}"
    return f"{_api().watch_summary(d)}\ntoken {d['token']}{msgs}"


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("watch",),
        summary="wait for a job, a file line, a process or a gate, renewing your leases; exit 2 = not yet (resume with the token)",
        tool="ddflow_watch",
        family="ddflow_lease",
        action="watch",
        tier="all",
        help_group="Doing the work",
        description=(
            "Wait for ONE thing and return what happened, renewing your leases meanwhile: a job (job: exit status), a line of a file (file + until: the matching line), a process (pid) or a gate (gate ITEM:GATE: its outcome). Use it instead of an until-loop, a pgrep or a heartbeat script. The wait is bounded: exit 2 means not yet and carries a `token`; call again with only that token to go on from where it stopped (a file is not re-read). `heartbeat` names the items whose leases to renew (default: every lease you hold)."
        ),
        call=lambda repo, a, agent: _api().watch_target(
            repo,
            job=a.get("job", "") or "",
            file=a.get("file", "") or "",
            until=a.get("until", "") or "",
            pid=int(a.get("pid") or 0),
            gate=a.get("gate", "") or "",
            heartbeat=a.get("heartbeat", "") or "",
            token=a.get("token", "") or "",
            timeout_s=_wait_timeout(a, repo),
            poll_s=a.get("poll"),
            agent=agent,
        ),
        payload="",
        render=_text,
        flag_exempt={
            "--poll": "a tuning knob for a shell caller; over MCP the default is right",
        },
        params=(
            Param(
                "job",
                help="A job id (`job list`): done when its process is over.",
                cli_help="a job id: done when it is over; prints its exit status",
                default="",
            ),
            Param(
                "file",
                help="A file to read; with `until`.",
                cli_help="a file; with --until",
                default="",
            ),
            Param(
                "until",
                help="With file: a regular expression a complete line must match.",
                cli_help="with --file: regex a complete line must match",
                default="",
            ),
            Param(
                "pid",
                type="integer",
                help="A process id: done when it is gone.",
                cli_help="a process id: done when it is gone",
            ),
            Param(
                "gate",
                help="ITEM:GATE: done when the gate has an outcome.",
                cli_help="ITEM:GATE: done when the gate has an outcome",
                default="",
            ),
            Param(
                "heartbeat",
                help="Comma-separated items whose leases to renew (default: every lease you hold).",
                cli_help="items whose leases to renew, comma-separated (default: all you hold)",
                default="",
            ),
            Param(
                "token",
                help="Resume a watch that timed out: give only this.",
                cli_help="resume a timed-out watch: give only this",
                default="",
            ),
            Param(
                "timeout",
                type="number",
                help=f"Seconds to wait (default {MCP_WAIT_DEFAULT_S}, at most {MCP_WAIT_MAX_S}); call again with the token to go on.",
                cli_help=f"seconds to wait (default {DEFAULT_WAIT_TIMEOUT_S}; 0 looks once)",
            ),
            Param(
                "poll",
                type="number",
                help="Seconds between probes (default 2).",
                cli_help="seconds between probes",
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
