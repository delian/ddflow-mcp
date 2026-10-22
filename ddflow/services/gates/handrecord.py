"""A command gate's pass is `gate run`'s to give, not an agent's to type (bug Bed643a611b).

`gate record --outcome passed --command 'pytest -q' --exit-code 0` on a gate that has a
command, with no run, put a green `unit_tests` or `ci` into the log on the agent's word. The
project rule is that a tool that could not run is recorded `unavailable`, never `passed`.
So an AGENT's hand-recorded pass of a command gate is always refused: a real run's pass is
recorded by `gate run` (or `ci run`), which do not come through here, and a second typed pass
over it would only replace the run's evidence with the agent's word. The operator's overrides
are untouched: a person's terminal, `gate skip`.
"""

from __future__ import annotations

from typing import Any

#: The tools that run a command gate's command themselves and record the result through
#: `gate record`'s API (`Evidence.ran_by`). Any other value is not trusted.
CI_RUN = "ddflow ci run"
RAN_BY = frozenset({CI_RUN})


def _refusal(it, gdef, gate: str, outcome: str, agent_why: str) -> str:
    """Why an agent's hand-recorded ``outcome`` for ``gate`` is refused, else ""."""
    if outcome != "passed" or not agent_why or gdef is None or not gdef.is_command_gate:
        return ""
    return (
        f"{gate} runs a command, and a pass of it is `ddflow gate run {it.id} {gate}`'s to "
        f"give: typing a command and an exit code records what the caller says, not what "
        f"ran ({agent_why}). Run it; or, if it could not run, record `--outcome unavailable "
        f"--reason ...` (or `partial`). Setting a gate aside is the operator's "
        f"(`ddflow gate skip {it.id} {gate} --reason ...` from their terminal)."
    )


def _gaps(command: str, output_file: str, tree: Any, host: str) -> dict[str, Any]:
    """What a hand-recorded gate lacks that `gate run` measures (bug Bb305b43f56): the fields
    ddflow could not take, named, and the host that holds a recorded ``--output-file`` (a path
    on the recording agent's machine, as a run's `output_log_host`). ``tree`` is None when the
    item's tree was not measured."""
    lacking = [] if tree is not None else ["tree_sha"]
    if command:
        lacking += ["elapsed_s", "command_digest"]  # only a run measures these
    out: dict[str, Any] = {"unmeasured": lacking} if lacking else {}
    if output_file and host:
        out["output_file_host"] = host
    return out


def hand_record(
    it, gdef, gate: str, outcome: str, agent_why: str, tree: Any, evidence, host: str, *, skip: bool
) -> tuple[str, dict[str, Any]]:
    """(a refusal, or the evidence to add) for ``gate record``'s ``evidence`` (an `Evidence`).
    A record a ddflow tool made after running the command (``evidence.ran_by``) is not a hand
    record: it is neither refused nor annotated."""
    if skip or evidence.ran_by in RAN_BY:
        return "", {}
    if why := _refusal(it, gdef, gate, outcome, agent_why):
        return why, {}
    return "", _gaps(evidence.command, evidence.output_file, tree, host)
