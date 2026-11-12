"""`ddflow gate run` and `ddflow_gate_run` take the SAME arguments (r-mode-parity).

The forward ratchet (`test_mcp_parity.py`) holds every CLI *command* to a tool. This holds
one command to its tool at the ARGUMENT level, because a lane that scripts the gate from
its brief and passes a flag one surface silently refuses loses the run if it does not read
the exit code. The only arguments one surface has and the other does not must be declared
(`Param.cli_only` / `Param.mcp_only`): a difference in the *public* set is drift, and the
reported `--full` gap is exactly the drift this pins out.
"""

from __future__ import annotations

from ddflow.surfaces.declared import all_commands
from ddflow.surfaces.mcp import TOOLS


def _gate_run_params():
    commands = [c for c in all_commands(tools=True) if c.path == ("gate", "run")]
    assert len(commands) == 1, commands
    return commands[0].params


def _mcp_props() -> set[str]:
    return set(TOOLS["ddflow_gate_run"]["properties"])


def test_gate_run_cli_and_mcp_expose_the_same_arguments():
    # The MCP properties are exactly the declared parameters that are not `cli_only`;
    # the CLI arguments are exactly those that are not `mcp_only`. A parameter not on
    # the CLI must be `mcp_only` and one not on the MCP tool must be `cli_only`, and a
    # mismatch needs no special case because each declaration is both surfaces at once.
    params = {p.name: p for p in _gate_run_params()}
    assert _mcp_props() == {n for n, p in params.items() if not p.cli_only}

    cli_names = {n for n, p in params.items() if not p.mcp_only}
    cli_only = {n for n, p in params.items() if p.cli_only}
    assert {"force", "reason", "rerun_failed"} <= cli_only


def test_full_is_carried_by_both_surfaces_or_neither():
    # The intent allows either resolution -- both surfaces accept `full`, or the MCP
    # argument goes -- so once `full` is one surface's argument it must be the other's
    # too. One-sided is the drift the report found, and this fails on it without forcing
    # a choice between the two resolutions the item leaves open.
    cli_names = {p.name for p in _gate_run_params() if not p.mcp_only}
    assert ("full" in _mcp_props()) == ("full" in cli_names)
