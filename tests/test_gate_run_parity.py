"""`ddflow gate run` and `ddflow_gate_run` take the SAME arguments (r-mode-parity).

The forward ratchet (`test_mcp_parity.py`) holds every CLI *command* to a tool. This holds
one command to its tool at the ARGUMENT level, because a lane that scripts the gate from
its brief and passes a flag one surface silently refuses loses the run if it does not read
the exit code. The only arguments one surface has and the other does not must be declared
(`Param.cli_only` / `Param.mcp_only`): a difference in the *public* set is drift, and the
reported `--full` gap (the MCP tool once documented `full` while the CLI refused it as
unrecognized) is exactly the drift this pins out.
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
    # The MCP properties are exactly the CLI arguments that are not declared CLI-only.
    # A public argument one surface has and the other does not (like `full` used to be)
    # fails this, so neither surface may grow one unilaterally.
    public = {p.name for p in _gate_run_params() if not p.cli_only}
    assert _mcp_props() == public


def test_a_cli_only_argument_is_declared_not_drift():
    # `--force`/`--reason`/`--rerun-failed` are CLI-only on purpose (they mutate lease and
    # evidence handling a remote script has no stake in). `full` is on NEITHER surface:
    # it must not reappear on the MCP tool while the CLI has no `--full` flag.
    cli_only = {p.name for p in _gate_run_params() if p.cli_only}
    assert {"force", "reason", "rerun_failed"} <= cli_only
    assert "full" not in _mcp_props() and "full" not in cli_only


def test_the_detector_can_fail_on_a_one_sided_full():
    # A `full` MCP property with no CLI twin would diverge the surfaces: name the drift so
    # the assertion that catches it is not vacuously true.
    mcp_only = _mcp_props() - {p.name for p in _gate_run_params()}
    assert "full" not in mcp_only
