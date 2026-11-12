"""`ddflow gate run` and `ddflow_gate_run` take the SAME arguments (r-mode-parity).

The forward ratchet (`test_mcp_parity.py`) holds every CLI *command* to a tool. This holds
one command to its tool at the ARGUMENT level, because a lane that scripts the gate from
its brief and passes a flag one surface silently refuses loses the run if it does not read
the exit code. The two surfaces are generated from one `Param` declaration, so this
compares the RENDERED surfaces (the argparse parser and the MCP schema), and a difference
must be declared (`cli_only` / `mcp_only`). The reported `--full` gap -- one surface
carrying `full` the other refuses -- is the drift this pins out.
"""

from __future__ import annotations

import argparse

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import all_commands
from ddflow.surfaces.mcp import TOOLS

#: The root options mirrored onto every subparser (`--repo`, `--agent`, `--json`,
#: `--allow-older-version`, `--reason`); a command's own arguments are the rest.
GLOBAL_DESTS = {"repo", "agent", "json", "allow_older", "skew_reason", "version"}

#: A long option string read back to a flag name: `--rerun-failed` -> `rerun_failed`.
def _flag_name(option: str) -> str:
    return option.lstrip("-").replace("-", "_")


def _gate_run_params():
    commands = [c for c in all_commands(tools=True) if c.path == ("gate", "run")]
    assert len(commands) == 1, commands
    return commands[0].params


def _gate_run_own_actions():
    parser = build_parser()
    group = parser._subparsers._group_actions[0]
    gate = group.choices["gate"]
    subparsers = [a for a in gate._actions if isinstance(a, argparse._SubParsersAction)][0]
    run = subparsers.choices["run"]
    return [a for a in run._actions if a.dest not in GLOBAL_DESTS and a.dest != "help"]


def test_gate_run_mcp_properties_are_the_parameters_not_declared_cli_only():
    # The MCP schema carries exactly the parameters that are not `cli_only`. A flag the
    # tool carries but the CLI refuses is caught by the other test; this holds the tool's
    # half against the declaration.
    params = {p.name: p for p in _gate_run_params()}
    assert set(TOOLS["ddflow_gate_run"]["properties"]) == {
        n for n, p in params.items() if not p.cli_only
    }


def test_gate_run_cli_arguments_are_the_parameters_not_declared_mcp_only():
    # The CLI arguments (positionals and flags) are exactly the parameters not declared
    # `mcp_only`; a parameter off the CLI must be `mcp_only`, and the declared CLI-only
    # escapes (`--force`, `--reason`, `--rerun-failed`) stay declared.
    params = {p.name: p for p in _gate_run_params()}
    cli_dests = {a.dest for a in _gate_run_own_actions()}
    assert cli_dests == {n for n, p in params.items() if not p.mcp_only}
    assert {"force", "reason", "rerun_failed"} <= {n for n, p in params.items() if p.cli_only}


def test_full_is_carried_by_both_surfaces_or_neither():
    # The item leaves the resolution open -- both surfaces accept `full`, or the MCP
    # argument goes -- so once `full` is one surface's flag it must be the other's too.
    # Read from the OPTION STRING (`--full`), which is exactly the argparse token a lane
    # writes and the CLI would otherwise refuse as unrecognized.
    cli_has_full = any(
        "--full" in a.option_strings for a in _gate_run_own_actions()
    )
    assert ("full" in TOOLS["ddflow_gate_run"]["properties"]) == cli_has_full
