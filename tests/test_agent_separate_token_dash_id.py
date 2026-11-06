"""A relaunch with a leading-dash identity survives as one ``--agent=<name>`` token
(bug B-agent-separate-token-dash-id).

``DDFLOW_AGENT`` and the agent-name pattern both allow a leading dash, and ``--agent -dry-run``
as two argv tokens is an argparse error: the relaunch exits 2 with "expected one argument". The
three relaunch sites that re-declare the identity as ``["--agent", agent]`` bound it to
``--agent=<value>`` (one token), the form argparse reads verbatim, so a name that starts with a
dash no longer breaks the relaunch. These tests read the argv through the real parser, so they
pin the identity the child gets, not how the flag is spelled.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ddflow.services.lanes import opencode
from ddflow.services.swarm import lanehooks as LH
from ddflow.services.upgrade_dryrun._rehearsal import Rehearsal
from ddflow.surfaces.cli import build_parser


def _parsed_agent(argv: list[str]) -> str:
    """The leading ``-m <module>`` prologue python itself consumes is not ddflow's argv."""
    return build_parser().parse_args(argv).agent


def test_opencode_mcp_entry_binds_a_leading_dash_identity(tmp_path: Path) -> None:
    """`:ddflow/services/lanes/opencode.py` builds ``--agent=<name>``, never two tokens."""
    argv = opencode.mcp_entry(tmp_path, "-dry-run")["command"]
    assert "--agent=-dry-run" in argv, "the identity is one token, not two"
    assert _parsed_agent(["--repo", str(tmp_path), "--agent=-dry-run", "mcp"]) == "-dry-run"


def test_lanehooks_check_argv_binds_a_leading_dash_identity(tmp_path: Path) -> None:
    """`:ddflow/services/swarm/lanehooks.py` binds the identity as one token."""
    argv = LH.check_argv(tmp_path, "-dry-run")
    assert "--agent=-dry-run" in argv, "the identity is one token, not two"


class _Capturing:
    def env(self, cmd: object) -> dict:
        return {}

    def capture(self, argv: list[str], **kwargs: object) -> list[str]:
        self.argv = argv  # type: ignore[attr-defined]
        return argv


def test_rehearsal_dryrun_binds_a_leading_dash_identity(tmp_path: Path) -> None:
    """`:ddflow/services/upgrade_dryrun/_rehearsal.py` binds the identity as one token."""
    cap = _Capturing()
    Rehearsal.ddflow(
        cap, SimpleNamespace(argv=["ddflow"], pythonpath=None), tmp_path, agent="-dry-run"
    )
    assert "--agent=-dry-run" in cap.argv, "the identity is one token, not two"
