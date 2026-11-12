"""`ddflow gate run` and `ddflow_gate_run` take the SAME arguments (r-mode-parity).

The forward ratchet (`test_mcp_parity.py`) holds every CLI *command* to a tool. This holds
one command to its tool at the ARGUMENT level, because a lane that scripts the gate from
its brief and passes a flag one surface silently refuses loses the run if it does not read
the exit code. The two surfaces are generated from one `Param` declaration, so the test
compares the RENDERED surfaces (the argparse parser and the MCP schema), not the
declaration, and a difference must be declared (`cli_only` / `mcp_only`). The reported
`--full` gap -- one surface carrying `full` the other refuses -- is the drift this pins out.
"""

from __future__ import annotations

import argparse

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import all_commands
from ddflow.surfaces.mcp import TOOLS

#: The root options mirrored onto every subparser (`--repo`, `--agent`, `--json`,
#: `--allow-older-version`, `--reason`); a command's own arguments are the rest.
GLOBAL_DESTS = {"repo", "agent", "json", "allow_older", "skew_reason", "version"}


def _gate_run_params():
    commands = [c for c in all_commands(tools=True) if c.path == ("gate", "run")]
    assert len(commands) == 1, commands
    return commands[0].params


def _gate_run_cli_dests() -> set[str]:
    parser = build_parser()
    group = parser._subparsers._group_actions[0]
    gate = group.choices["gate"]
    subparsers = [a for a in gate._actions if isinstance(a, argparse._SubParsersAction)][0]
    run = subparsers.choices["run"]
    return {a.dest for a in run._actions if a.dest != "help"} - GLOBAL_DESTS


def test_gate_run_cli_and_mcp_expose_the_same_arguments():
    params = {p.name: p for p in _gate_run_params()}
    # The MCP schema's properties are exactly the parameters not declared `cli_only`, and
    # the rendered CLI arguments are exactly those not declared `mcp_only`. Asserts
    # against the two RENDERED surfaces, so either one may drift from the other only by a
    # declaration.
    assert set(TOOLS["ddflow_gate_run"]["properties"]) == {
        n for n, p in params.items() if not p.cli_only
    }
    assert _gate_run_cli_dests() == {n for n, p in params.items() if not p.mcp_only}
    assert {"force", "reason", "rerun_failed"} <= {n for n, p in params.items() if p.cli_only}


def test_full_is_carried_by_both_surfaces_or_neither():
    # The item leaves the resolution open -- both surfaces accept `full`, or the MCP
    # argument goes -- so once `full` is carried by one surface it must be the other's
    # too. A gate run CLI that refuses `--full` while the tool carries `full` (the
    # observed drift) fails this, so it fails on the drift without choosing a resolution.
    assert ("full" in TOOLS["ddflow_gate_run"]["properties"]) == ("full" in _gate_run_cli_dests())
