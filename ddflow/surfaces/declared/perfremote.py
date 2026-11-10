"""The `perf` command, declared once: the host fingerprint and the measuring wrapper.

`perf env` prints the machine-class fingerprint (never a hostname or path); `perf run`
returns the exact wrapped command an agent runs where ddflow cannot (`ddflow perf run --
CMD`); `perf ingest` accepts one reported sample line at the reported trust level. The
parser half is registered by convention and the MCP half is `ddflow_perf` (D-unify 4).
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _call(repo, a, agent):
    action = a.get("action") or "env"
    if action == "env":
        return _api().run_perf_env(repo, agent=agent)
    if action == "run":
        cmd = a.get("cmd") or ""
        if isinstance(cmd, list):
            import shlex

            cmd = " ".join(shlex.quote(part) for part in cmd)
        return _api().run_perf_run(repo, cmd=cmd, agent=agent)
    return _api().run_perf_ingest(repo, line=a.get("line") or "", agent=agent)


def _text(out, a) -> str:
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("perf",),
        summary="perf of the host: the machine-class fingerprint and the measuring wrapper",
        tool="ddflow_perf",
        description=(
            "Perf data about where ddflow runs. action=env: this machine class's fingerprint "
            "(a stable key over the class of machine, never a hostname, user or path, plus its "
            "run-time condition, facts and container flag). action=run: the exact wrapped "
            "command an agent runs where ddflow cannot run it itself -- `ddflow perf run -- CMD` "
            "-- whose wrapper prints one JSON line of wall, cpu, rss and io. action=ingest + line: "
            "accept one such reported sample line (an agent-reported timing is kept at the "
            "agent-reported trust level and never counted as gate evidence of a pass). Malformed "
            "input is refused, never recorded as zero."
        ),
        call=_call,
        payload=("text",),
        render=_text,
        params=(
            Param(
                "action",
                help="env (the fingerprint), run (measure a command) or ingest (accept a sample line).",
                cli_help="env (default), run or ingest",
                positional=True,
                nargs="?",
                choices=("env", "run", "ingest"),
            ),
            Param(
                "cmd",
                help=(
                    "with action=run: the command to measure. CLI: `ddflow perf run --cmd 'pytest -q'`."
                ),
                cli_help="with run: the command to measure",
            ),
            Param(
                "line",
                help="with action=ingest: the wrapper's one JSON line to accept.",
                cli_help="with ingest: the sample line",
            ),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
