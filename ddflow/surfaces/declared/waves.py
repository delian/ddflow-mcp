"""`ddflow waves`: dependency waves, `--stamp` (write the derived `wave:N` tags), `--check`.

The derivation is `services/waves.py` and the writes ride `api/batch.apply`, the engine
behind `ddflow update --add-tags/--remove-tags` (D-wave-semantics). Stamping also happens
by default after a command that changed the graph (knob `[waves].auto_stamp`).
"""

from __future__ import annotations

import importlib

from ...core import outcome as O
from ..registry import CliPolicy, Command, Param


def _call(repo, args, agent):
    return importlib.import_module("ddflow.api.waves").report(repo, args, agent)


def _text(out, a) -> str:
    return importlib.import_module("ddflow.api.waves").text(out, a)


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("waves",),
        summary="print the dependency waves; --stamp writes the derived wave:N tags, --check exits 1 when they are stale",
        tool="ddflow_waves",
        family="ddflow_view",
        action="waves",
        tier="all",
        help_group="Reading the queue",
        description="Dependency waves of the open queue: wave 0 = no open prerequisite "
        "(task-level or inherited from a phase), wave N = 1 + the highest wave among open "
        "prerequisites; an item on a cycle has none. `stamp` writes the derived wave:N tags "
        "in ONE transaction, only for items whose wave changed (a hand edit of wave:* is "
        "overwritten; idempotent). `check` returns exit 1 when stamps are stale. Stamping "
        "also runs by default after a graph change: knob [waves].auto_stamp.",
        wire_shape=((["waves"], {})),
        call=_call,
        payload=("waves", "cyclic", "stale", "stamped"),
        render=_text,
        cli=CliPolicy(shown=(O.OK, O.FAIL)),
        params=(
            Param("stamp", type="boolean", help="Write the derived wave:N tags."),
            Param("check", type="boolean", help="Exit 1 when any wave:N stamp is stale."),
            Param(
                "limit",
                type="integer",
                help="Items listed per wave in the text view (default 10; 0 = all).",
                default=10,
            ),
        ),
    ),
)
