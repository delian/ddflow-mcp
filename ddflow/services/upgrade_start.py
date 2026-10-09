"""Upgrade at start: an MCP server or container that finds itself newer than its project.

Decisions D-self-upgrade (5) and D-upgrade-on-mcp-connect. When ``ddflow mcp`` (or the
container that runs it) starts, it compares the running ddflow with the one this project was
last brought up to (`upgrade_plan.project_version`) and, per ``[upgrade].on_start`` (or the
``DDFLOW_UPGRADE_ON_START`` environment variable, which wins for one run):

* ``off``: does nothing;
* ``check``: builds the read-only plan and PROPOSES it -- the handshake instructions and the
  first brief say what is pending and how to apply it once the operator agrees;
* ``safe`` (default): also rebuilds the derived stores (the sqlite index), and -- in a
  CONTAINER, where the operator chose to run the newer image -- applies the plan's
  non-destructive categories (`upgrade_notice.SAFE_CATEGORIES`) with a backup first. On an MCP
  connect outside a container the operator's permission comes first (D-upgrade-on-mcp-connect:
  "nothing is applied without that consent"), so only the derived stores are rebuilt and the
  rest is proposed.

Three promises hold in every mode:

* it NEVER fails the start: every error becomes a line in the report, and a step that runs
  past ``[upgrade].start_timeout_s`` is left running and reported as pending;
* a project it cannot write (a read-only mount, a volume owned by another uid) gets the plan
  and a line saying nothing was written;
* a runtime OLDER than the project writes nothing: it says ``restart/upgrade`` (the skew guard,
  D-upgrade-skew-guard, refuses its writes anyway).

The outcome is kept in ``.ddflow/local/upgrade-start.json`` (machine-local, git-ignored) so the
first brief can repeat it once for an agent that never saw the handshake.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import is_older, version_key
from ..core.model import State, fold
from ..infra import fsio
from ..infra.log import running_version
from ..infra.store import Store
from . import upgrade_notice as UN
from . import upgrade_plan as UP

#: Machine-local, beside `upgrade-notice.json`.
MARKER = Path(".ddflow") / "local" / "upgrade-start.json"
#: The environment switch the image documents; it wins over `[upgrade].on_start` for one run.
ENV = "DDFLOW_UPGRADE_ON_START"
#: Set by the image (Dockerfile): the surface is a container, not a person's own MCP client.
CONTAINER_ENV = "DDFLOW_IN_CONTAINER"
MODES = ("off", "check", "safe")
STRICTEST = (
    "check"  #: what an unknown ``DDFLOW_UPGRADE_ON_START`` counts as (D-enum-fallback-strict)
)

# Report statuses.
OFF, CURRENT, OLDER, PROPOSED, APPLIED, READONLY, TIMEOUT, FAILED = (
    "off",
    "current",
    "older",
    "proposed",
    "applied",
    "readonly",
    "timeout",
    "failed",
)


def surface(environ: Mapping[str, str] | None = None) -> str:
    """``container`` when the image says so, else ``mcp``."""
    env = os.environ if environ is None else environ
    return "container" if env.get(CONTAINER_ENV) else "mcp"


def mode(cfg: Config, environ: Mapping[str, str] | None = None) -> str:
    """The policy for this start: the environment variable if set, else the knob."""
    env = os.environ if environ is None else environ
    raw = str(env.get(ENV, "")).strip().lower()
    if raw:
        return raw if raw in MODES else STRICTEST
    return cfg.upgrade.on_start


def _writable(repo: Path) -> bool:
    """Can this process write the project's ``.ddflow``? ``os.access`` answers for the real
    uid and notices a read-only mount, which a ``stat`` of the mode bits would not."""
    ddflow = Path(repo) / ".ddflow"
    local = ddflow / "local"
    return os.access(ddflow, os.W_OK | os.X_OK) and (
        not local.exists() or os.access(local, os.W_OK | os.X_OK)
    )


def _read(repo: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(repo) / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(repo: Path, report: dict[str, Any]) -> None:
    """Best effort: a marker that cannot be written costs only a repeat of the line."""
    path = Path(repo) / MARKER
    previous = _read(repo)
    keep = previous.get("text") == report.get("text") and previous.get("version") == report.get(
        "version"
    )
    body = {
        "version": report.get("version", ""),
        "status": report.get("status", ""),
        "text": report.get("text", ""),
        "briefed": bool(keep and previous.get("briefed")),
    }
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps(body) + "\n")
    except OSError:
        pass


def _bounded(work: Callable[[], Any], seconds: float) -> tuple[bool, Any]:
    """Run ``work`` for at most ``seconds``. ``(True, result)``, or ``(False, None)`` when it
    is still running (it is left to finish in the background: a daemon thread, never joined at
    exit) -- a start must not wait on a slow migration. A raised error is re-raised here."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = work()
        except BaseException as exc:  # reported by the caller, never lost in a thread
            box["error"] = exc

    thread = threading.Thread(target=run, name="ddflow-upgrade-start", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


def _categories(plan: dict[str, Any]) -> str:
    cats = plan.get("categories", {})
    return ", ".join(f"{n} {len(cats[n])}" for n in UP.CATEGORIES if cats.get(n))


_ASK = (
    " Ask the operator whether to upgrade now (`ddflow upgrade --plan` shows each item); only "
    "on their yes call `ddflow_upgrade` with `apply` (a config value anyone set also needs "
    "`confirm` and `reason`), or run `ddflow upgrade --apply`."
)


def _proposal(plan: dict[str, Any], running: str, project: str) -> str:
    """The read-only plan as the operator's question (D-upgrade-on-mcp-connect)."""
    since = f"was last brought up under {project}" if project else "predates version stamps"
    return (
        f"ddflow {running} is newer than this project, which {since}: "
        f"{plan['total']} upgrade item(s) are pending ({_categories(plan)}). Nothing has "
        "been applied." + _ASK
    )


def _older(running: str, highest: str) -> str:
    return (
        f"this ddflow ({running}) is OLDER than the one that last worked on this project "
        f"({highest}): nothing was changed and its writes are refused until it is upgraded -- "
        "restart the server on the newer version (reconnect the MCP server in your client)."
    )


def _work(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    *,
    agent: str,
    surf: str,
    policy: str,
    running: str,
) -> dict[str, Any]:
    """The start's work for a project that is behind. Raises on a surprise; `start` tells it."""
    plan = UP.build(repo, log, cfg, st, running=running)
    project = str(plan.get("project_version", ""))
    if plan["up_to_date"]:
        return {"status": CURRENT, "text": ""}
    if policy == "check":
        return {"status": PROPOSED, "text": _proposal(plan, running, project)}
    if not _writable(repo):
        return {
            "status": READONLY,
            "text": (
                f"ddflow {running} found {plan['total']} upgrade item(s) pending "
                f"({_categories(plan)}) but this project is not writable from here, so nothing "
                "was written: run `ddflow upgrade --apply` where it is."
            ),
        }
    did: list[str] = []
    try:
        Store(repo, cfg).rebuild(log)
        did.append("rebuilt the index")
    except Exception as exc:  # a derived store: say so, carry on
        did.append(f"could not rebuild the index ({exc})")
    if surf == "container":
        # `_apply_safe` never raises: a failed step is told, not thrown.
        said = UN.apply_safe(repo, log, cfg, st, plan, agent)
        if said:
            did.append(said)
        plan = UP.build(repo, log, cfg, _refold(log, st), running=running)
    head = (
        f"ddflow {running} is newer than this project (last brought up under "
        f"{project or 'no version stamp'})"
    )
    text = f"{head}: {'; '.join(did)}."
    if not plan["up_to_date"]:
        text += f" {plan['total']} item(s) still pending ({_categories(plan)})." + _ASK
    return {"status": APPLIED, "text": text}


def _refold(log: Any, st: State) -> State:
    """The state after the apply wrote events; the old one when it cannot be read again."""
    try:
        return fold(log.read_all(), strict=False)
    except Exception:
        return st


def start(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    *,
    agent: str = "",
    surf: str = "",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the start check for ``repo``; returns the report ``{status, version, text}``.

    NEVER raises and never returns after more than ``[upgrade].start_timeout_s`` plus a small
    constant: ``text`` is "" when there is nothing to say. Also records the report for
    `take_for_brief`."""
    repo = Path(repo)
    running = running_version()
    report: dict[str, Any] = {"status": OFF, "version": running, "text": ""}
    try:
        if not (repo / ".ddflow").is_dir() or not version_key(running):
            return report  # a directory ddflow never adopted: a marker would adopt it
        policy = mode(cfg, environ)
        if policy == "off":
            return report
        highest = str(getattr(st, "highest_version", "") or "")
        if highest and is_older(running, highest):
            report.update(status=OLDER, text=_older(running, highest))
        else:
            has_history = bool(st.ddflow_versions or st.upgrades or st.items or st.sessions)
            project = UP.project_version(st, has_history, running)
            if project and not is_older(project, running):
                report["status"] = CURRENT
            else:
                seconds = float(cfg.upgrade.start_timeout_s)
                surf = surf or surface(environ)

                def work() -> dict[str, Any]:
                    return _work(
                        repo, log, cfg, st, agent=agent, surf=surf, policy=policy, running=running
                    )

                done, out = (False, None) if seconds <= 0 else _bounded(work, seconds)
                if done:
                    report.update(out)
                else:
                    report.update(
                        status=TIMEOUT,
                        text=(
                            f"ddflow {running} is newer than this project; the upgrade check "
                            f"did not finish within {seconds:g}s, so the server started "
                            "anyway and the upgrade stays pending: run `ddflow upgrade --plan`."
                        ),
                    )
    except Exception as exc:  # the one promise: never fail the start
        report.update(
            status=FAILED,
            text=f"the upgrade check at start failed ({exc}); serving anyway. "
            "Run `ddflow upgrade --plan`.",
        )
    if _writable(repo) and (report["text"] or (repo / MARKER).exists()):
        # An empty report is saved too when a marker exists: the project caught up (the
        # operator applied the upgrade by hand), so the old proposal must not be replayed.
        _save(repo, report)
        if report["text"]:
            UN.told(repo, running, report["text"])  # the one-line notice would only repeat it
    return report


def take_for_brief(repo: Path) -> str:
    """The recorded start report, once, for the first brief; "" when none or already given.

    A marker that cannot be rewritten (a read-only store) still delivers the text: the cost
    is that the next brief repeats it, not that the operator is never told."""
    try:
        data = _read(repo)
        text = str(data.get("text", ""))
        if not text or data.get("briefed"):
            return ""
    except Exception:
        return ""
    try:
        data["briefed"] = True
        fsio.atomic_write(Path(repo) / MARKER, json.dumps(data) + "\n")
    except Exception:
        pass
    return text
