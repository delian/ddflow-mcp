"""The merge queue command, declared once, with the tool it has (DX-merge-queue).

`surfaces/parsers/maintenance.py` registers its command-line half and
`surfaces/tools/__init__.py` takes its MCP entry. The operations are `api.mergequeue_add`
(the agent's half) and `api.mergequeue_run` (the integrator)."""

from __future__ import annotations

from ...core import outcome as O
from ...core.outcome import FAIL, OK
from ..registry import CliPolicy, Command, Param, by_tool
from ..tools._common import _api
from . import mergequeue_cli as M

ACTIONS = ("add", "run", "status", "remove")


def _call(repo, a, agent, called_from=None):
    api = _api()
    action = a.get("action") or "status"
    if action in ("add", "remove") and not a.get("id"):
        return O.refused("queue", f"queue {action} needs an item id")
    unit_tests = a.get("unit_tests")
    if unit_tests is None and a.get("no_unit_tests"):
        unit_tests = False
    if action == "add":
        return api.queue_add(repo, a["id"], unit_tests=unit_tests, agent=agent)
    if action == "remove":
        return api.queue_remove(repo, a["id"], agent=agent)
    if action == "run":
        return api.queue_run(
            repo,
            max_batch=a.get("max_batch"),
            unit_tests=unit_tests,
            timeout_s=a.get("timeout"),
            agent=agent,
            called_from=called_from,
        )
    return api.queue_status(repo, agent=agent)


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("queue",),
        summary="the merge queue: add an item, run the integrator, show the line, remove an entry",
        tool="ddflow_queue",
        description=(
            "Merge queue. add: line up a finished item (all gates but ci/merge done). "
            "run: the integrator (any agent; one at a time, else exit 2): per entry merges base, "
            "regenerates pins, runs ci once, lands it; failures are parked. "
            "status: order and ETA. remove."
        ),
        call=_call,
        payload="",
        wants_called_from=True,
        cli=CliPolicy(shown=(OK, FAIL), body_always=True),
        render=M.text,
        params=(
            Param(
                "action",
                positional=True,
                nargs="?",
                default="status",
                choices=ACTIONS,
                help="add | run | status (default) | remove.",
                cli_help="add ITEM: enqueue it; run: integrate the head (any agent may); "
                "status (default): the line; remove ITEM: drop an entry",
            ),
            Param(
                "id",
                positional=True,
                nargs="?",
                default="",
                help="Item (add, remove).",
                cli_help="the item (add, remove)",
            ),
            Param(
                "max_batch",
                type="integer",
                help="run: max entries.",
                cli_help="run: integrate at most this many entries (default [merge_queue].max_batch)",
            ),
            Param(
                "unit_tests",
                type="boolean",
                default=None,
                help="Also run unit_tests on the merged result.",
                cli_help="run/add: also run each entry's unit_tests gate on the merged result",
            ),
            Param(
                "no_unit_tests",
                type="boolean",
                cli_only=True,
                cli_help="run/add: do not run unit_tests (overrides [merge_queue].run_unit_tests)",
            ),
            Param(
                "timeout",
                type="integer",
                help="run: ci seconds per entry.",
                cli_help="run: seconds one entry's ci may run (default [merge_queue].timeout_s)",
            ),
        ),
        tool_order=("action", "id", "max_batch", "unit_tests", "timeout"),
        flag_exempt={"--no-unit-tests": "the tool takes unit_tests=false"},
    ),
)

BY_TOOL = by_tool(COMMANDS)
