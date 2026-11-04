"""The Lane contract: how ddflow starts, watches, steers and stops a coding agent.

One small interface, one driver per TRANSPORT (research driver-architecture 4.4, D-DRV-4).
``process-headless`` is built (``lane_headless``); ACP, Pi RPC, OpenCode HTTP and tmux plug in
later through ``register_transport`` behind the same five calls plus a capability set.
The contract and the registry are the port `ddflow.adapters.lanes` (ADP-agent-lanes); this
module re-exports them, so a transport imports it and nothing else of ddflow's lane code, and
keeps the descriptor glue (`spec_from_headless`).

Rules every driver keeps: launch is an argv list, never a shell line; secrets travel by env
NAME and their values stay out of every repr, event and status; the lane gets a private home;
cleanup is one process-group kill with an optional scope; a capability the transport does not
have is reported ``unavailable`` and never imitated. See docs/ddflow/lane-driver.md.
"""

from __future__ import annotations

import shlex
from typing import Any

from ..adapters.lanes import (
    BUILTIN_TRANSPORTS,
    CAPABILITIES,
    RESULT_STATUSES,
    STATES,
    TRANSPORTS,
    Lane,
    LaneEvent,
    LaneHandle,
    LaneStatus,
    LaunchSpec,
    Result,
    advertise,
    driver_for,
    failed,
    ok,
    register_transport,
    transports,
    unavailable,
)
from .engines import limit_detect, stream_map
from .harnessv2 import Autonomy, Headless


def lane_options(cfg: Any) -> dict[str, Any]:
    """The ``[lanes]`` knobs a launch uses, as ``LaunchSpec`` keywords (a ``Config`` in)."""
    return {
        "stop_grace_s": float(cfg.lanes.stop_grace_s),
        "event_buffer": int(cfg.lanes.event_buffer),
        "scope": cfg.lanes.scope,
        "pass_env": tuple(cfg.lanes.pass_env),
    }


def spec_from_headless(
    name: str,
    headless: Headless,
    *,
    autonomy: Autonomy | None = None,
    stream_rules: tuple[stream_map.Rule, ...] = (),
    limit: limit_detect.Spec | None = None,
    model: str = "",
    effort: str = "",
    max_turns: int = 0,
    mcp_config: str = "",
    session: str = "",
    **spec: Any,
) -> LaunchSpec | Result:
    """A ``LaunchSpec`` from a descriptor's ``[headless]`` section.

    A flag the descriptor names is added with its ``{placeholder}`` value only when the template
    does not already carry that placeholder and the caller gave a value. Missing pieces make an
    ``unavailable`` Result (no argv, ``none = true``, a resume requested without a resume flag,
    an autonomy profile that does not exist), never a guess."""
    if headless.none or not headless.argv:
        return unavailable("the descriptor has no [headless] launch template")
    argv = list(headless.argv)
    values: dict[str, str] = {"dir": spec.get("cwd") or ""}
    for flag, ph, val in (
        (headless.model_flag, "model", model),
        (headless.effort_flag, "effort", effort),
        (headless.max_turns_flag, "max_turns", max_turns or ""),
        (headless.mcp_flag, "mcp_config", mcp_config),
        (headless.resume_flag, "session", session),
    ):
        if val in ("", None):
            continue
        values[ph] = str(val)
        if "{" + ph + "}" in argv:
            continue  # the template already carries it: no flag needed
        if not flag:
            return unavailable(f"the descriptor has no flag for {ph}; it is not guessed")
        argv += [flag, "{" + ph + "}"]
    if headless.autonomy:
        profile = (autonomy.profiles if autonomy and not autonomy.none else {}).get(
            headless.autonomy
        )
        if profile is None:
            return unavailable(f"autonomy profile {headless.autonomy!r} is not in [autonomy]")
        argv += shlex.split(profile)  # static descriptor data split into args, no shell
    if headless.output_flag:
        argv += [
            headless.output_flag,
            *([headless.output_format] if headless.output_format else []),
        ]
    return LaunchSpec(
        name=name,
        argv=tuple(argv),
        values={k: v for k, v in values.items() if v != ""},
        stream_rules=stream_rules,
        limit=limit,
        exit_codes=dict(headless.exit_codes),
        resume=bool(headless.resume_flag),
        **spec,
    )


__all__ = [
    "BUILTIN_TRANSPORTS",
    "CAPABILITIES",
    "RESULT_STATUSES",
    "STATES",
    "TRANSPORTS",
    "Lane",
    "LaneEvent",
    "LaneHandle",
    "LaneStatus",
    "LaunchSpec",
    "Result",
    "advertise",
    "driver_for",
    "failed",
    "lane_options",
    "ok",
    "register_transport",
    "spec_from_headless",
    "transports",
    "unavailable",
]
