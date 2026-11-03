"""The `archdeps` command: the dependency census, declared once.

One read-only report of every external dependency family (third-party packages and the
stdlib families the adapter migration tracks) with its importing modules, declared home and
violations. The ratchet that fails a NEW importer is `tests/test_arch_deps.py`.
"""

from __future__ import annotations

from ..registry import Command, Param
from ..tools._common import _api


def _text(out, a) -> str:
    return out.data["text"].rstrip("\n")


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("archdeps",),
        summary="the external-dependency census: families, importing modules, home, violations",
        description=(
            "The external-dependency census as a read-only report: every dependency family "
            "(third-party packages and the stdlib families the adapter migration tracks) with "
            "its importing modules, declared home and violations; the ratchet that fails a new "
            "importer is what keeps the report honest. Read-only."
        ),
        tool="ddflow_archdeps",
        tier="standard",
        reason="",
        wire_shape=(["archdeps"], {}),
        call=lambda repo, a, agent: _api().archdeps(
            repo,
            agent=agent,
            interfaces=a.get("interfaces") or "",
            baseline=bool(a.get("baseline")),
        ),
        payload=("families", "interfaces", "ratchet"),
        render=_text,
        params=(
            Param(
                "interfaces",
                default="",
                metavar="KIND",
                help="Census the interfaces that are not imports: 'all' or one kind "
                "(program, protocol, managed-file, env, network): programs run, wire "
                "protocols, other tools' files, environment variables, network endpoints.",
            ),
            Param(
                "baseline",
                type="boolean",
                help="With interfaces: print the ratchet baseline text instead of the report.",
            ),
        ),
        family="ddflow_view",
        action="archdeps",
        help_group="view",
    ),
)
