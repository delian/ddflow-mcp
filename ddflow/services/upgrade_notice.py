"""The upgrade notice: ddflow was upgraded, and the project has not been told.

Decision D-upgrade-auto-check (operator, 2026-10-03). After ddflow is upgraded, the first
brief (and with it the SessionStart hook) or MCP handshake on a machine says, in ONE line,
``Upgraded ddflow 0.1.9 -> 0.1.10: run `ddflow upgrade --plan``` -- once per version
on this machine, per project -- and changes nothing else. ``[upgrade].auto`` governs it:

* ``check`` (the default): the line only;
* ``safe``: the line, after applying the plan's non-destructive categories (``hooks`` and
  ``instructions``) with a backup first. Config defaults, migrations, repairs and features
  stay the operator's: they change behaviour or data, not just ddflow's own files;
* ``off``: nothing.

A notice is due when the running version is newer than the one the project was last brought
up to (`upgrade_plan.project_version`) and the plan has items. It is recorded in
``.ddflow/local/upgrade-notice.json`` (machine-local, git-ignored) so that whichever surface
speaks first -- the brief, the hook, the MCP instructions -- speaks for all of them, and the
others stay quiet. The marker is checked BEFORE the plan is built, so the common case (already
said, or nothing to say) costs one small file read.

The other half is the STALE SERVER: an MCP server is a long-lived process, and one started
before an upgrade keeps running the old code. `stale_server_note` compares the code that is
running with the installed package and with the highest version stamped in the log, and says
``restart the server`` -- the caller says it once per server.

Nothing here raises: a notice is a courtesy on top of the answer the caller asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import is_older, version_key
from ..core.model import State
from ..infra import fsio
from ..infra.log import running_version
from . import upgrade_apply as UA
from . import upgrade_plan as UP
from .install_info import install_info

#: Machine-local, beside `seen.json`: the version this machine has already been told about.
MARKER = Path(".ddflow") / "local" / "upgrade-notice.json"

#: The plan categories `[upgrade].auto = safe` applies by itself: ddflow's own files (hooks,
#: driver docs and rules blocks), each backed up first and idempotent.
SAFE_CATEGORIES = ("hooks", "instructions")


def noticed_version(root: Path | str) -> str:
    """The version this machine was last told about, "" when it never was."""
    try:
        data = json.loads((Path(root) / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("version", "")) if isinstance(data, dict) else ""


def _mark(root: Path | str, version: str, said: str) -> None:
    """Record that ``version`` was dealt with. Best effort: a marker that cannot be written
    costs only a repeat of the line."""
    path = Path(root) / MARKER
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps({"version": version, "said": said}) + "\n")
    except OSError:
        pass


def told(root: Path | str, version: str, said: str) -> None:
    """Record that ``version`` was already said (by the start report), so the one-line notice
    stays quiet. Never moves the marker backwards."""
    seen = noticed_version(root)
    if version_key(version) and (not seen or is_older(seen, version)):
        _mark(root, version, said)


def _behind(project: str, running: str) -> bool:
    """Is the project older than the running ddflow? A project last worked on before versions
    were stamped (``""``) is."""
    return not project or is_older(project, running)


def apply_safe(
    repo: Path, log: Any, cfg: Config, st: State, plan: dict[str, Any], agent: str
) -> str:
    """Apply `SAFE_CATEGORIES` with a backup first. Returns what to say about it, "" when the
    plan had nothing in those categories to do. NEVER raises: a failure is told, not thrown."""
    try:
        done = UA.apply(
            repo,
            log,
            cfg,
            st,
            categories=list(SAFE_CATEGORIES),
            confirm={},
            backup=cfg.upgrade.backup,
            agent=agent,
            plan=plan,
        )
    except Exception as exc:  # a snapshot refused on a dirty tree, an unreadable file...
        return f"could not apply {' and '.join(SAFE_CATEGORIES)} by itself ({exc})"
    applied = sum(1 for r in done.get("results", []) if r.get("status") == "applied")
    if not applied and not done.get("failed"):
        return ""
    detail = f"{applied} item(s)"
    if done.get("backup"):
        detail += f", backup {done['backup']}"
    if done.get("failed"):
        detail += f", {done['failed']} step(s) failed"
    return f"applied {' and '.join(SAFE_CATEGORIES)} ({detail})"


def line(repo: Path, log: Any, cfg: Config, st: State, *, agent: str = "") -> str:
    """The one-line notice, or "" when none is due. Marks the version as told.

    ``log``, ``cfg`` and ``st`` are the caller's own (the brief already holds all three)."""
    try:
        policy = cfg.upgrade.auto
        if policy == "off" or not (Path(repo) / ".ddflow").is_dir():
            return ""  # off, or a directory ddflow never adopted: writing a marker would adopt it
        running = running_version()
        told = noticed_version(repo)
        # A marker at THIS version or a NEWER one: a long-lived MCP server still running
        # pre-upgrade code must neither repeat the line nor move the marker backwards.
        if not version_key(running) or (told and not is_older(told, running)):
            return ""
        # Claim the version BEFORE the (slow) plan is built: the hook and the MCP handshake
        # start together after an upgrade, and the later of two readers of an empty marker
        # would say it twice. The final mark below only adds what was said.
        _mark(repo, running, "")
        plan = UP.build(repo, log, cfg, st, running=running)
        project = str(plan.get("project_version", ""))
        if plan["up_to_date"] or not _behind(project, running):
            return ""
        said = ""
        if policy == "safe":
            said = apply_safe(repo, log, cfg, st, plan, agent)
        text = (
            f"Upgraded ddflow {project} -> {running}"
            if project
            else f"Upgraded ddflow to {running} (this project predates version stamps)"
        )
        if said:
            text += f"; {said}"
        text += ": run `ddflow upgrade --plan`"
        _mark(repo, running, text)
        return text
    except Exception:
        return ""


def installed_version() -> str:
    """The version of the ddflow package installed NOW (distribution metadata, read from
    disk each time), "" when it cannot be told. A running process keeps the version it was
    imported at; after an upgrade the two differ."""
    try:
        return install_info().version
    except Exception:
        return ""


def stale_server_note(st: State, running: str = "", installed: str = "") -> str:
    """``restart the server`` when this process runs older code than the installed package or
    the log's highest stamp, else "". Pure over its arguments (the caller reads them)."""
    running = running or running_version()
    if not version_key(running):
        return ""
    newest, where = running, ""
    for version, label in (
        (installed, "the installed package"),
        (getattr(st, "highest_version", ""), "this project's log"),
    ):
        if version_key(version) and is_older(newest, version):
            newest, where = version, label
    if not where:
        return ""
    return (
        f"note: this ddflow MCP server is running {running}, but {where} is at {newest}: "
        "restart the server (reconnect the MCP server in your client) to pick up the upgrade."
    )
