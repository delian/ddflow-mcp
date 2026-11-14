"""`ddflow stacks`: the build, smoke, lock-check and test commands this repository's stack resolves to."""

from __future__ import annotations

import importlib

from ...core import outcome as O
from ..registry import CliPolicy, Command


def _call(repo, args, agent):
    return importlib.import_module("ddflow.api.stacks").report(repo, args, agent)


def _text(out, a) -> str:
    return importlib.import_module("ddflow.api.stacks").text(out, a)


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("stacks",),
        summary="show the stack this repository resolves to and its build, smoke, lock-check and test commands",
        tool="ddflow_stacks",
        family="ddflow_view",
        action="stacks",
        tier="all",
        help_group="Reading the queue",
        description="Resolve this repository against the stack table (shipped "
        "ddflow/templates/stacks/*.toml plus the project's [verify].stacks_dir): which stack, "
        "the command per role (build, smoke, lock_check, test), where each came from (table or "
        "a [verify] override) and whether its program is on PATH. A repository no entry "
        "describes is exit 2 with the config command that fixes it; a missing tool is "
        "unavailable, never passed. Nothing is run.",
        wire_shape=((["stacks"], {})),
        call=_call,
        payload=("stack", "status", "detail", "next_call", "also", "warnings", "evidence", "steps"),
        render=_text,
        cli=CliPolicy(shown=(O.OK, O.NOTHING)),
        params=(),
    ),
)
