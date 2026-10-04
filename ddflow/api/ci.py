"""`ddflow ci run|status`: the CI parity gate's command (D-ci-parity)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import ci as CI
from ._base import _load


def run(
    repo: Path, *, ref: str = "HEAD", base: str = "", command: str = "", agent: str = ""
) -> O.Outcome:
    """Run the CI command on `ref` merged with `base`. 0 passed, 1 failed, 2 could not run."""
    _log, cfg, _st = _load(repo, agent)
    res = CI.run(repo, cfg, ref=ref, base=base, command=command)
    data = res.as_data()
    if res.ok:
        return O.ok("ci.run", **data)
    if res.status == "failed":
        return O.Outcome(kind="ci.run", data=data, exit=O.FAIL, reason=res.reason)
    return O.nothing("ci.run", res.reason, **data)


def status(repo: Path, *, agent: str = "") -> O.Outcome:
    """What `ci run` would execute here, and whether it can."""
    _log, cfg, _st = _load(repo, agent)
    cmd, why = CI.resolve_command(repo, cfg)
    missing = CI.tool_missing(cmd) if cmd else ""
    data = {
        "command": cmd,
        "base": cfg.ci.base or "(default branch)",
        "timeout_s": cfg.ci.timeout_s,
        "available": bool(cmd) and not missing,
        "why": why or (f"{missing!r} is not installed" if missing else ""),
    }
    return O.ok("ci.status", **data)


def ci_tool(
    repo: Path,
    *,
    action: str = "status",
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    agent: str = "",
) -> O.Outcome:
    """The `ddflow_ci` tool: action run | status."""
    if action == "run":
        return run(repo, ref=ref or "HEAD", base=base, command=command, agent=agent)
    if action == "status":
        if base or command or (ref and ref != "HEAD"):
            return O.refused("ci", "ref, base and command are for action=run")
        return status(repo, agent=agent)
    return O.refused("ci", f"unknown action {action!r}: run | status")
