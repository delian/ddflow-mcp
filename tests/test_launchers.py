"""`launchers` reports every ddflow hook command whose launcher is gone (bug B4af8a88294).

`check_settings` recognised ddflow's own commands by the SessionStart and prompt markers
only, so a PreCompact hook (`ddflow hooks pre-compact`) pointing at a deleted venv was
never reported: `doctor` and `hooks status` stayed quiet while the hook silently fell
back or failed open.
"""

from __future__ import annotations

import pytest

from ddflow.services import claudehooks as CH
from ddflow.services import enforce as E
from ddflow.services import launchers as LA


def _dangling(marker: str) -> str:
    line = E.command_line(marker, refresh="ddflow hooks install --claude")
    for p in LA.recorded_paths(line):
        line = line.replace(p, "/nonexistent-ddflow-venv" + p)
    assert LA.recorded_paths(line), "the line records no launcher, so this proves nothing"
    return line


@pytest.mark.parametrize(
    ("event", "marker", "matcher"),
    [
        ("SessionStart", CH.MARKER, CH.MATCHER),
        (CH.PRECOMPACT_EVENT, CH.PRECOMPACT_MARKER, None),
        (CH.PROMPT_EVENT, CH.PROMPT_MARKER, None),
    ],
)
def test_every_ddflow_hook_command_with_a_dead_launcher_is_reported(repo, event, marker, matcher):
    CH.install(repo, _dangling(marker), event=event, marker=marker, matcher=matcher)

    found = LA.check_settings(repo)

    assert len(found) == 1, f"the {event} hook's dead launcher went unreported: {found}"
    assert ".claude/settings.json" in found[0].where
    assert "ddflow hooks install --claude" in found[0].render()
