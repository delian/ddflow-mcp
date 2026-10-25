"""`ddflow waves` -- the scheduled wave surface, reporting its real self: not built yet.

`ddflow waves` (compute dependency waves) and `--stamp` (write derived wave:N labels the
way D-wave-semantics fixes them) arrive with LB-waves. This command exists in the
declarative registry so the missing `--stamp` is reported WITH the followable interim
mechanism (the roadmap-restamp script behind `ddflow update --add-tags`/`--remove-tags`)
instead of argparse's bare `invalid choice: 'waves'` (bug B47acb25115). It implements
neither compute nor stamping: both are LB-waves' job.
"""

from __future__ import annotations

from ...core.outcome import nothing
from ..registry import CliPolicy, Command, Param

_SHOWN = CliPolicy(shown=None)

#: The report the command exists to print, on every exit: what is missing, what ships it,
#: and what to follow meanwhile (docs/ddflow/decisions/token-spend.md, bug B47acb25115).
_MISSING = (
    "`ddflow waves --stamp` is missing: wave computation and stamping are scheduled as "
    "LB-waves and not implemented yet (bug B47acb25115)."
)
_GUIDANCE = (
    "Until LB-waves ships `ddflow waves --stamp`, the followable mechanism is the "
    "roadmap-review restamp script `docs/ddflow/research/scripts/wave_stamp_accurate.py` "
    "(via `ddflow update --add-tags`/`--remove-tags`)."
)


def _call(repo, args, agent):
    return nothing(
        "waves.missing",
        f"{_MISSING} {_GUIDANCE}",
        stamp=bool(args.get("stamp")),
        scheduled="LB-waves",
    )


def _text(out, a) -> str:
    return out.reason


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("waves",),
        summary="report that `--stamp` is not implemented and name the interim wave-stamping mechanism (LB-waves ships it)",
        tool="",
        reason=(
            "CLI-only until LB-waves implements it: the command reports the missing "
            "`--stamp` with its guidance and runs nothing an agent would call mid-task."
        ),
        call=_call,
        payload=("stamp", "scheduled"),
        render=_text,
        cli=_SHOWN,
        params=(
            Param(
                "stamp",
                type="boolean",
                help="write derived wave:N labels (not implemented yet; LB-waves ships it).",
            ),
        ),
    ),
)
