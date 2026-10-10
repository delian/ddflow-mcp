"""The release check: is there a newer ddflow than this one, and has the operator been told.

Decision D-self-upgrade (1) and (2). At most once per ``[upgrade].check_interval_h`` ddflow asks
the package index it was installed from (`infra.release_index`) for the newest released
version, remembers the answer in ``.ddflow/local/release-check.json`` (machine-local,
git-ignored, never committed), and PROPOSES the upgrade -- never performs it. The check is
opportunistic (a brief, `doctor`, `ddflow upgrade --check`), never a daemon, and it can
neither block nor fail the command that triggered it: offline, a timeout, a bad reply, a
read-only store all end in silence. Nothing but the request for the public version list leaves
the machine.

It is off when ``[upgrade].release_check`` is ``off`` or the environment has
``DDFLOW_NO_UPDATE_CHECK`` set (to anything but ``0``, ``false``, ``no`` or ``off``): then no
request is made at all, and a cached answer is no longer spoken.

A ddflow running from a source checkout is never proposed an upgrade by the periodic check
(its newer version is a merge, not a release) and makes no request on its own.

The proposal line is said ONCE per version on this machine, by the first surface that speaks
(the brief or the MCP handshake: `proposal(consume=True)`); `status`, `doctor` and
`ddflow upgrade --check` read the cache and say it whenever a newer release is known.
Time and transport are arguments, so tests need neither a network nor a sleep.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import is_older
from ..infra import fsio, release_index
from ..infra.log import running_version
from . import install_info as II

#: Machine-local, beside the upgrade notice: the last answer and the version already proposed.
CACHE = Path(".ddflow") / "local" / "release-check.json"
ENV_OFF = "DDFLOW_NO_UPDATE_CHECK"
Clock = Callable[[], float]


def env_off() -> bool:
    """Is the check switched off by the environment (``DDFLOW_NO_UPDATE_CHECK``)?"""
    return os.environ.get(ENV_OFF, "").strip().lower() not in ("", "0", "false", "no", "off")


def enabled(cfg: Config) -> bool:
    return cfg.upgrade.release_check == "on" and not env_off()


def _dev_tree() -> bool:
    """Is this ddflow running from a source checkout? Then the index's newest release says
    nothing about it (its upgrade is a merge), so the OPPORTUNISTIC check stays silent and
    makes no request; `ddflow upgrade --check` still asks when the operator does."""
    return II.running_from_source()


def read_cache(repo: Path | str) -> dict[str, Any]:
    """The cached answer, ``{}`` when there is none or it cannot be read."""
    data = fsio.read_json(Path(repo) / CACHE)
    return dict(data) if isinstance(data, dict) else {}


def _write_cache(repo: Path | str, data: dict[str, Any]) -> None:
    """Best effort: a cache that cannot be written costs a repeat request, nothing else."""
    path = Path(repo) / CACHE
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps(data, sort_keys=True) + "\n")
    except OSError:
        pass


def _due(cache: dict[str, Any], cfg: Config, now: float) -> bool:
    at = cache.get("checked_at")
    if not isinstance(at, (int, float)) or at > now:
        return True  # never checked, or a clock that moved back
    # A failed request (offline, timeout) counts as a check: at most one request per interval,
    # and a machine without a network pays the timeout once a day, not in every brief.
    return now - at >= max(0.0, float(cfg.upgrade.check_interval_h)) * 3600.0


def newer_known(repo: Path | str, running: str = "") -> str:
    """The cached newest release when it is newer than ``running``, else ""."""
    running = running or running_version()
    newest = str(read_cache(repo).get("newest", ""))
    return newest if newest and is_older(running, newest) else ""


def check(
    repo: Path | str,
    cfg: Config,
    *,
    force: bool = False,
    fetch: release_index.Fetch | None = None,
    now: float | None = None,
    running: str = "",
) -> dict[str, Any]:
    """Ask the index if the cache is stale (or ``force``), and report.

    Returns ``status`` -- ``off`` (switched off: NO request), ``cached`` (still fresh: no
    request), ``checked`` (asked and answered) or ``offline`` (asked, no usable answer) --
    with ``running``, ``newest`` (the last known, "" if never), ``newer`` and ``checked_at``.
    Never raises, and never adopts a directory ddflow was not set up in."""
    running = running or running_version()
    when = time.time() if now is None else now
    cache = read_cache(repo)
    out: dict[str, Any] = {"running": running, "newest": str(cache.get("newest", ""))}
    if not enabled(cfg):
        out["status"] = "off"
    elif not (Path(repo) / ".ddflow").is_dir() or not (force or _due(cache, cfg, when)):
        out["status"] = "cached"
    else:
        try:
            newest = release_index.newest(
                II.DIST_NAME,
                index_url=cfg.upgrade.index_url,
                prereleases=cfg.upgrade.prereleases,
                fetch=fetch,
            )
            cache.update(checked_at=when, newest=newest, error="")
            out.update(status="checked", newest=newest)
        except Exception as exc:  # ReleaseIndexError, or anything an injected fetch raised
            cache.update(checked_at=when, error=f"{type(exc).__name__}: {exc}"[:200])
            out.update(status="offline", error=cache["error"])
        _write_cache(repo, cache)
    out["newer"] = bool(out["newest"]) and is_older(running, out["newest"])
    out["checked_at"] = cache.get("checked_at")
    return out


def how(newest: str) -> str:
    """The command that brings THIS install to ``newest`` (fitted to how it was installed)."""
    return II.upgrade_advice(newest)


def line(running: str, newest: str) -> str:
    return (
        f"ddflow {newest} is available (you run {running}): {how(newest)}. "
        "Tell the operator; `ddflow upgrade --check` says what it is."
    )


def proposal(
    repo: Path | str,
    cfg: Config,
    *,
    consume: bool = False,
    refresh: bool = True,
    fetch: release_index.Fetch | None = None,
    now: float | None = None,
    running: str = "",
) -> str:
    """The one-line proposal to upgrade ddflow, or "" when there is none to make.

    Refreshes the cache when it is due (unless ``refresh`` is false: cache alone), then speaks when a newer release is known. With
    ``consume`` the version is recorded as proposed and the line is not repeated for it
    (the session-start surfaces); without, it is said whenever it holds (status, doctor).
    Never raises."""
    try:
        if not enabled(cfg) or _dev_tree():
            return ""
        running = running or running_version()
        if refresh:
            check(repo, cfg, fetch=fetch, now=now, running=running)
        newest = newer_known(repo, running)
        if not newest:
            return ""
        if consume:
            cache = read_cache(repo)
            if cache.get("proposed") == newest:
                return ""
            cache["proposed"] = newest
            _write_cache(repo, cache)
        return line(running, newest)
    except Exception:
        return ""


def known_line(repo: Path | str, cfg: Config, running: str = "") -> str:
    """The proposal from the CACHE alone -- no request, no write -- or "": for `status`,
    which is asked often and must stay a read."""
    try:
        running = running or running_version()
        newest = newer_known(repo, running) if enabled(cfg) and not _dev_tree() else ""
        return line(running, newest) if newest else ""
    except Exception:
        return ""


def report(
    repo: Path | str,
    cfg: Config,
    *,
    project_version: str = "",
    fetch: release_index.Fetch | None = None,
    now: float | None = None,
    running: str = "",
) -> dict[str, Any]:
    """`ddflow upgrade --check`: force a check and say what it found, as plain data plus
    ``text``. ``project_version`` is the project's stamp (`upgrade_plan.project_version`)."""
    info = check(repo, cfg, force=True, fetch=fetch, now=now, running=running)
    info["project_version"] = project_version
    info["install"] = II.install_info().kind
    parts = [f"running ddflow {info['running']}"]
    parts.append(f"project stamp {project_version or '(none: predates version stamps)'}")
    if info["status"] == "off":
        parts.append(
            "release check is OFF ([upgrade].release_check or DDFLOW_NO_UPDATE_CHECK): "
            "no request was made"
        )
    elif info["status"] == "offline":
        known = f"; last known newest {info['newest']}" if info["newest"] else ""
        parts.append(f"the release index could not be reached ({info.get('error', '')}){known}")
    elif not info["newest"]:
        parts.append("the release index could not be asked")
    elif info["newer"]:
        parts.append(f"newest release {info['newest']}: {how(info['newest'])}")
        if project_version and is_older(project_version, info["running"]):
            parts.append("this project is behind the running ddflow: `ddflow upgrade --plan`")
        parts.append(
            "what the new version changes is listed by `ddflow upgrade --plan` once it is installed"
        )
    else:
        parts.append(f"newest release {info['newest']}: this ddflow is the newest")
    info["text"] = "\n".join(parts)
    return info
