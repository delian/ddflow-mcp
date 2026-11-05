"""`ddflow probe`, declared once: the conflict probe between an item and main or a peer.

`surfaces/parsers/by_convention.py` adds the command-line half and
`surfaces/tools/__init__.py` the MCP entry (D-unify 4). Reads git, writes one
`probe.recorded` event; no lock, no daemon (D-sched-no-daemon)."""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("probe",),
        summary="would an item's branch merge clean with main or a peer? (exit 1 = conflict, 2 = could not tell)",
        tool="ddflow_probe",
        tier="standard",
        help_group="Doing the work",
        family="ddflow_lease",
        action="probe",
        description=(
            "Conflict probe: runs `git merge-tree --write-tree` between an item's branch and "
            "main (default) or another item, in milliseconds, and records the answer with both "
            "tip shas. Exit 0 clean, 1 conflict (the paths are named), 2 unavailable (git too "
            "old and no scratch merge possible, timeout, unknown ref, [locks].probe off): never "
            "read as clean. Clean means TEXTUALLY clean only; a renamed function the other side "
            "uses still needs the gates and ci on the merge result."
        ),
        call=lambda repo, a, agent: _api().conflict_probe(
            repo, a["id"], against=a.get("against") or "", at=a.get("at") or "", agent=agent
        ),
        payload=(
            "id",
            "against",
            "result",
            "method",
            "paths",
            "head",
            "base",
            "why",
            "text",
        ),
        render=lambda out, a: out.data["text"],
        params=(
            Param("id", help="The item whose branch is probed.", cli_help="", positional=True),
            Param(
                "against",
                help="main (default) or another item id (or a branch name).",
                cli_help="main (default), another item id or a branch",
                default="",
            ),
            Param(
                "at",
                help="Where this probe is made: claim, gate or merge; skipped when [locks].probe_at does not list it.",
                cli_help="claim, gate or merge: skipped when [locks].probe_at does not list it",
                choices=("claim", "gate", "merge"),
                default="",
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
