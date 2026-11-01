"""The `archdeps` command: the dependency census, declared once.

One read-only report of every external dependency family (third-party packages and the
stdlib families the adapter migration tracks) with its importing modules, declared home and
violations. The ratchet that fails a NEW importer is `tests/test_arch_deps.py`.
"""

from __future__ import annotations

from ..registry import Command
from ..tools._common import _api


def _text(out, a) -> str:
    return out.data["text"].rstrip("\n")


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("archdeps",),
        summary="the external-dependency census: families, importing modules, home, violations",
        tool="ddflow_archdeps",
        tier="standard",
        reason="",
        call=lambda repo, a, agent: _api().archdeps(repo, agent=agent),
        payload=("families",),
        render=_text,
        params=(),
        family="ddflow_view",
        action="archdeps",
        help_group="view",
    ),
)
