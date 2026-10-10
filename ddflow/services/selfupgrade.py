"""Upgrade ddflow itself: `ddflow upgrade --self [--to VERSION] [--dry-run] [--rollback]`.

Decision D-self-upgrade (3) and (4). The release check (`upgrade_check`) PROPOSES a newer
ddflow; this carries it out -- only on request, only with the operator's consent, only where
ddflow knows the right command.

* **The method decides the command** (`install_info.install_method`): ``uv-tool`` runs
  ``uv tool upgrade``, ``pipx`` ``pipx upgrade``, ``pip-venv`` that interpreter's own
  ``pip install -U``. Anything else -- an ephemeral ``uvx`` environment, a container image, a
  source checkout, an install this user cannot write, a system pip -- is NOT attempted: the
  exact command for the operator is printed and the answer is exit 2 (could not run).
* **Consent** (``[upgrade].self``): ``ask`` (default) runs it only with ``--yes --reason``
  (MCP ``confirm=["self"]`` and ``reason``), recorded in the event; ``operator`` refuses an
  agent over MCP altogether; ``auto`` needs no consent. Without it the call is REFUSED
  (exit 3) and shows the proposal. ``--dry-run`` names the command and runs nothing.
* **The installer is a subprocess** with a bounded time, its output captured. The running
  process never replaces its own code mid-command: it finishes, records
  ``upgrade.applied`` (category ``self``), remembers the previous version in
  the user's state directory (``self-upgrade.json``) and says RESTART (an MCP server reports the same).
* **A failure leaves the old version working**: the installers swap atomically, nothing is
  recorded, and the answer says which version still runs.
* **Migration follows the install** (D-self-upgrade 4): the NEW code, in a fresh process, shows
  the project plan and, under ``[upgrade].auto = safe``, applies its non-destructive
  categories after a backup.
* **Rollback** reinstalls the remembered previous version the same way, with the same consent.

Transport is injectable (``runner``) so tests need neither a network nor an installer.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import UPGRADE_APPLIED_KIND, version_key
from ..infra import proc as P
from ..infra.localstore import LocalStore
from ..infra.log import running_version
from . import install_info as II
from . import upgrade_notice as UN

#: Seconds the installer may take before it is stopped (a hung index must not hang a session).
INSTALL_TIMEOUT_S = 600.0
#: Seconds the new code may take to show the plan.
PLAN_TIMEOUT_S = 120.0
#: The output kept of a failed installer.
KEEP_OUTPUT = 1500
STATE = "self-upgrade.json"
CONSENT = "self"

Runner = Callable[[list[str], float], P.Captured]

#: Why the method is not attempted, and what the operator runs instead (``{dist}``, ``{spec}``).
_MANUAL = {
    "container": "pull the image{tag} and restart the container: docker pull <your-ddflow-image>{tag_colon}",
    "source": "update the checkout{tag}, then restart the MCP server: git pull (merge main)",
    "uvx": "an ephemeral environment: run it again with a refresh: uvx --refresh --from {spec} ddflow ...",
    "readonly": "this install is not writable by this user: ask whoever owns it, or reinstall with: uv tool install --force {spec}",
    "other": "ddflow does not guess how this was installed; reinstall it the way you installed it, e.g.: uv tool install --force {spec}   (or: pipx install --force {spec}   or: pip install -U {spec})",
}


def _spec(target: str) -> str:
    return f"{II.DIST_NAME}=={target}" if target else II.DIST_NAME


def command(method: str, target: str = "") -> list[str] | None:
    """The installer argv for ``method`` bringing ddflow to ``target`` ("" = the newest), or
    None when ddflow does not run an installer for it."""
    spec = _spec(target)
    if method == "uv-tool":
        if target:
            return ["uv", "tool", "install", "--force", spec]
        return ["uv", "tool", "upgrade", II.DIST_NAME]
    if method == "pipx":
        if target:
            return ["pipx", "install", "--force", spec]
        return ["pipx", "upgrade", II.DIST_NAME]
    if method == "pip-venv":
        return [sys.executable, "-m", "pip", "install", "-U", spec]
    return None


def manual(method: str, target: str = "") -> str:
    """What the operator runs for a method ddflow does not attempt."""
    return _MANUAL.get(method, _MANUAL["other"]).format(
        spec=_spec(target),
        tag=f" for version {target}" if target else "",
        tag_colon=f":{target}" if target else "",
    )


def _state() -> LocalStore:
    """The previous version belongs to the INSTALL, not to a project: it lives in the user's
    state directory (``$XDG_STATE_HOME/ddflow``, default ``~/.local/state/ddflow``), so a global
    tool upgraded from any directory can be rolled back from any directory."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return LocalStore(Path(base) / "ddflow")


def previous_version() -> str:
    """The version before the last successful self-upgrade ("" when there was none)."""
    try:
        data = _state().read(STATE, default={}, derived=True)
    except Exception:
        return ""
    return str(data.get("previous", "")) if isinstance(data, dict) else ""


def _remember(previous: str, now: str, method: str) -> None:
    try:
        _state().write(STATE, {"previous": previous, "now": now, "method": method}, derived=True)
    except Exception:
        pass  # a store that cannot be written costs the rollback, not the upgrade


def _default_runner(argv: list[str], timeout_s: float) -> P.Captured:
    return P.capture(argv, timeout=timeout_s)


def consent_problem(cfg: Config, confirm: list[str], reason: str, surface: str) -> str:
    """Why this call may not run an installer, "" when it may (D-self-upgrade 3)."""
    policy = cfg.upgrade.self
    if policy == "auto":
        return ""
    if policy == "operator" and surface == "mcp":
        return (
            "[upgrade].self = operator: an agent does not upgrade ddflow; the operator runs "
            "`ddflow upgrade --self --yes --reason WHY` in a terminal"
        )
    if CONSENT not in confirm or not reason.strip():
        return (
            "installing a new ddflow needs the operator's consent: ask them, then pass "
            '`--yes --reason WHY` (MCP: confirm=["self"] and reason). It is recorded.'
        )
    return ""


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= KEEP_OUTPUT else "..." + text[-KEEP_OUTPUT:]


def _record(log: Any, frm: str, to: str, rollback: bool, method: str, reason: str) -> None:
    verb = "rollback" if rollback else "upgrade"
    log.append(
        UPGRADE_APPLIED_KIND,
        "upgrade",
        {
            "from": frm,
            "to": to,
            "categories": [CONSENT],
            "backup": "",
            "items": [f"self:{method}"],
            "confirmed": {CONSENT: reason} if reason else {},
            "config_changes": [],
            "summary": f"self-{verb} ddflow {frm} -> {to} ({method})",
        },
    )


def _follow(repo: Path, cfg: Config, runner: Runner, *, rollback: bool) -> tuple[str, str]:
    """Let the NEW code show the project plan (and apply the safe categories under
    ``[upgrade].auto = safe``): `(plan text, what it applied)`. Best effort."""
    base = [sys.executable, "-m", "ddflow", "--repo", str(repo)]
    plan = runner([*base, "upgrade", "--plan"], PLAN_TIMEOUT_S)
    # The plan exits 1 while it has items: that is its finding, not a failure.
    shown = plan.ran and plan.returncode in (0, 1)
    text = (
        _tail(plan.stdout)
        if shown
        else f"(the plan could not be shown: run `ddflow upgrade --plan`) {_tail(plan.stderr)}"
    )
    applied = ""
    if not rollback and cfg.upgrade.auto == "safe" and shown:
        cats = ",".join(UN.SAFE_CATEGORIES)
        done = runner([*base, "upgrade", "--apply", cats], PLAN_TIMEOUT_S)
        if done.ran and done.returncode == 0:
            applied = _tail(done.stdout)
        else:
            applied = f"FAILED, nothing was promised: run `ddflow upgrade --apply {cats}` ({_tail(done.stderr or done.stdout)})"
    return text, applied


def _installed_version(run_: Runner, fallback: str) -> str:
    """The version now installed, asked of the NEW code in a fresh process; ``fallback`` when
    it cannot be told."""
    done = run_([sys.executable, "-m", "ddflow", "--version"], 30.0)
    words = done.stdout.split() if done.ran and done.returncode == 0 else []
    return words[-1] if words and version_key(words[-1]) else fallback


@dataclass
class Request:
    """What the caller asked for: the surfaces fill it from `--self`'s flags."""

    target: str = ""
    dry_run: bool = False
    rollback: bool = False
    confirm: list[str] = field(default_factory=list)
    reason: str = ""
    surface: str = ""
    #: Facts the caller can pin (tests): how ddflow is installed, the version running, the
    #: newest release known.
    method: str = ""
    running: str = ""
    newest: str = ""


def _failure(out: dict[str, Any], done: P.Captured, running: str) -> dict[str, Any]:
    why = (
        f"timed out after {INSTALL_TIMEOUT_S:.0f}s"
        if done.timed_out
        else f"could not run ({done.error})"
        if not done.ran
        else f"exited {done.returncode}: {_tail(done.stderr or done.stdout)}"
    )
    text = f"{out['command']} {why}. ddflow {running} is still installed and working."
    return {**out, "status": "failed", "exit": 1, "text": text}


def _success(
    repo: Path, cfg: Config, log: Any, req: Request, out: dict[str, Any], run_: Runner
) -> dict[str, Any]:
    running, method = out["running"], out["method"]
    to = out["target"] or _installed_version(run_, req.newest)
    # After a rollback nothing earlier is known: a second rollback must not undo the first.
    _remember("" if req.rollback else running, to, method)
    if (repo / ".ddflow").is_dir():
        try:
            _record(log, running, to, req.rollback, method, req.reason)
        except Exception:
            pass  # the install happened; a log that cannot be written is told, not thrown
    plan, applied = _follow(repo, cfg, run_, rollback=req.rollback)
    verb = "rolled back" if req.rollback else "upgraded"
    lines = [
        f"{verb} ddflow {running} -> {to or 'the newest release'} with `{out['command']}`.",
        "RESTART needed: this process still runs the old code; restart your MCP server / "
        "reconnect your client.",
    ]
    if applied.startswith("FAILED"):
        lines.append(f"the [upgrade].auto = safe apply did not happen: {applied}")
    elif applied:
        lines.append(f"applied by [upgrade].auto = safe: {applied}")
    if plan:
        lines.append(f"project plan under the new ddflow:\n{plan}")
    return {**out, "status": "upgraded", "exit": 0, "to": to, "text": "\n".join(lines)}


def run(
    repo: Path, cfg: Config, log: Any, req: Request, *, runner: Runner | None = None
) -> dict[str, Any]:
    """Carry out `ddflow upgrade --self`. Returns plain data with ``status`` -- ``dry-run``,
    ``refused`` (consent), ``manual`` (not attempted), ``upgraded``, ``failed`` or ``nothing``
    -- ``exit`` (0, 3, 2, 0, 1, 2) and ``text``. Never raises for the installer's trouble."""
    running = req.running or running_version()
    method = req.method or II.install_method()
    target = previous_version() if req.rollback else req.target
    out: dict[str, Any] = {
        "method": method,
        "running": running,
        "target": target,
        "rollback": req.rollback,
    }
    if req.rollback and not target:
        text = "no earlier ddflow is remembered: nothing to roll back to"
        return {**out, "status": "nothing", "exit": 2, "text": text}
    argv = command(method, target)
    out["command"] = shlex.join(argv) if argv else ""
    if argv is None:
        text = f"ddflow was installed as {method}; it is not upgraded from here. {manual(method, target)}"
        return {**out, "status": "manual", "exit": 2, "text": text}
    if req.dry_run:
        return {**out, "status": "dry-run", "exit": 0, "text": f"would run: {out['command']}"}
    if why := consent_problem(cfg, req.confirm, req.reason, req.surface):
        return {**out, "status": "refused", "exit": 3, "text": f"{why}. Proposal: {out['command']}"}
    run_ = runner or _default_runner
    done = run_(argv, INSTALL_TIMEOUT_S)
    if not done.ran or done.returncode != 0:
        return _failure(out, done, running)
    return _success(repo, cfg, log, req, out, run_)


__all__ = ["Request", "command", "consent_problem", "manual", "previous_version", "run"]
