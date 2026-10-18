"""The `sync` command, declared once: merge an item's recorded base into its branch."""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _call(repo, a, agent):
    return _api().lifecycle.sync(
        repo, item=a.get("item") or "", check=bool(a.get("check")), agent=agent
    )


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("sync",),
        summary="merge the item's recorded base into its branch when that is safe; --check only measures",
        tool="ddflow_sync",
        tier="standard",
        help_group="Doing the work",
        description=(
            "Merge the item's recorded base branch into its worktree branch (never rebase) when safe: "
            "refuses with uncommitted changes, aborts on a conflict and names the files, reports how many "
            "merged commits touch the item's globs and which recorded gates stay valid. check=true measures only."
        ),
        wire_shape=(["sync", "NOPE", "--check"], {"item": "NOPE", "check": True}),
        call=_call,
        payload=(
            "item",
            "base",
            "status",
            "behind",
            "touching_globs",
            "pin_files",
            "conflicts",
            "dirty",
            "detail",
            "gates_valid",
            "gates_stale",
            "text",
        ),
        render=lambda out, a: out.data.get("text") or out.reason,
        params=(
            Param(
                "item",
                help="The item (default: the item whose worktree this is).",
                positional=True,
                nargs="?",
            ),
            Param("check", type="boolean", help="Only measure the drift; merge nothing."),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
