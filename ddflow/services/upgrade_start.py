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
  past ``[upgrade].start_timeout_s`` is left running and reported as pending -- a NOTE that
  says the upgrade is pending and asks the operator, never silence and never "current". The
  answer the step reaches later is kept (the marker, keyed by version, policy and surface) so
  the next connect proposes at once, and the brief repeats the note (``doctor_notes`` gives the
  same note to ``ddflow doctor``) until the
  project is upgraded or a check finishes;
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
from ..infra.log import EventLog, running_version
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

#: The statuses of a check that did not reach an answer.
UNFINISHED = (TIMEOUT, FAILED)
#: The statuses worth keeping as the answer of a start (see `_cacheable`).
_ANSWERS = (PROPOSED, READONLY, APPLIED)
#: The statuses whose note repeats (the brief and `ddflow doctor`) until the project is upgraded
#: (or a later check finishes): an unfinished check, and a proposal or partial apply that leaves
#: the project behind (not READONLY: no marker is written for a read-only project, so there is nothing to replay) -- an actionable upgrade is not dropped because one reader saw it once.
_REPEATING = (*UNFINISHED, PROPOSED, APPLIED)


def surface(environ: Mapping[str, str] | None = None) -> str:
    """``container`` when the image says so, else ``mcp``."""
    env = os.environ if environ is None else environ
    # Exactly what our image sets and `infra.container.in_container` trusts first: `1`. A
    # stray `true` or `0` is no container, and only a container may apply without asking.
    return "container" if env.get(CONTAINER_ENV) == "1" else "mcp"


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


#: One writer of the marker at a time in this process: the start's own thread saves its report
#: while the thread it left running may be saving the answer it reached late.
_SAVE_LOCK = threading.Lock()


def _save(repo: Path, report: dict[str, Any]) -> None:
    """Best effort: a marker that cannot be written costs only a repeat of the line."""
    with _SAVE_LOCK:
        _save_locked(repo, report)


def _save_locked(repo: Path, report: dict[str, Any]) -> None:
    path = Path(repo) / MARKER
    previous = _read(repo)
    if (
        report.get("status") in UNFINISHED
        and previous.get("complete")
        and previous.get("version") == report.get("version")
        and report.get("key", previous.get("key")) == previous.get("key")
    ):
        # a finished answer for this very start outranks the note that it ran late, and the
        # report of a failure (it knows no key)
        return
    keep = previous.get("text") == report.get("text") and previous.get("version") == report.get(
        "version"
    )
    body = {
        "version": report.get("version", ""),
        "status": report.get("status", ""),
        "text": report.get("text", ""),
        "briefed": bool(keep and previous.get("briefed")),
        "key": report.get("key", ""),
        "complete": report.get("status") not in UNFINISHED and bool(report.get("key")),
        "repeat": bool(report.get("repeat", True)),
    }
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps(body) + "\n")
    except OSError:
        pass


def _bounded(
    work: Callable[[], Any], seconds: float, late: Callable[[Any], None] | None = None
) -> tuple[bool, Any]:
    """Run ``work`` for at most ``seconds``. ``(True, result)``, or ``(False, None)`` when it
    is still running (it is left to finish in the background: a daemon thread, never joined at
    exit) -- a start must not wait on a slow migration. A raised error is re-raised here.

    When it finishes AFTER the wait, ``late(result)`` is called from that thread (an error
    there is dropped): the answer a slow check reaches is kept, not thrown away."""
    box: dict[str, Any] = {}
    lock = threading.Lock()

    def run() -> None:
        try:
            value, error = work(), None
        except BaseException as exc:  # reported by the caller, never lost in a thread
            value, error = None, exc
        with lock:
            abandoned = bool(box.get("abandoned"))
            if not abandoned:
                box.update(finished=True, value=value, error=error)
        if abandoned and error is None and late is not None:
            try:
                late(value)
            except Exception:
                pass

    thread = threading.Thread(target=run, name="ddflow-upgrade-start", daemon=True)
    thread.start()
    thread.join(seconds)
    with lock:
        if not box.get("finished"):
            box["abandoned"] = True
            return False, None
    if box.get("error") is not None:
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


def _contested(plan: dict[str, Any]) -> str:
    said = UP.contested_clause(plan)
    return f" {said}" if said else ""


def _proposal(plan: dict[str, Any], running: str, project: str) -> str:
    """The read-only plan as the operator's question (D-upgrade-on-mcp-connect)."""
    since = f"was last brought up under {project}" if project else "predates version stamps"
    return (
        f"ddflow {running} is newer than this project, which {since}: "
        f"{plan['total']} upgrade item(s) are pending ({_categories(plan)}). Nothing has "
        "been applied." + _contested(plan) + _ASK
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
        text += f" {plan['total']} item(s) still pending ({_categories(plan)})."
        text += _contested(plan) + _ASK
    return {"status": APPLIED, "text": text}


def _refold(log: Any, st: State) -> State:
    """The state after the apply wrote events; the old one when it cannot be read again."""
    try:
        return fold(log.read_all(), strict=False)
    except Exception:
        return st


def _key(running: str, project: str, policy: str, surf: str, writable: bool = True) -> str:
    """What an answer of the start check is good for: the same ddflow over a project at the
    same version, under the same policy and surface, as writable (a read-only answer is not
    replayed once the project can be written). The plan's item count may drift as the
    log grows; `ddflow upgrade --plan` is always the authority. The agent is not part of it:
    only a container's applying start uses the agent (it is never kept), and the text of a
    proposal does not name one."""
    return "|".join((running, project, policy, surf, "rw" if writable else "ro"))


def _cacheable(report: dict[str, Any], surf: str) -> bool:
    """A container's safe start APPLIES things, so it is re-run; any other answer is kept. An
    MCP start's "rebuilt the index" is kept too: the index rebuilds itself when a read finds it
    stale, so replaying the line costs nothing the next read does not pay."""
    status = report.get("status")
    return (
        bool(report.get("text"))
        and status in _ANSWERS
        and not (status == APPLIED and surf == "container")
    )


def _cached(repo: Path, key: str) -> dict[str, Any] | None:
    """The kept answer for ``key``: a start that ran late finished it for the next connect."""
    data = _read(repo)
    if data.get("complete") and data.get("key") == key and data.get("status") in _ANSWERS:
        return {"status": data["status"], "text": str(data.get("text", "")), "key": key}
    return None


def _timeout_note(running: str, project: str, seconds: float) -> str:
    since = f"was last brought up under {project}" if project else "predates version stamps"
    why = (
        "the check is switched off ([upgrade].start_timeout_s = 0)"
        if seconds <= 0
        else f"the upgrade check did not finish within {seconds:g}s"
    )
    return (
        f"ddflow {running} is newer than this project, which {since}: an upgrade is PENDING "
        f"but its plan is not known yet -- {why}, so the server started anyway and the upgrade "
        "stays pending (nothing had been applied when it started; a container's safe work may "
        "still be running). Run `ddflow upgrade --plan` to see it (a later connect or brief "
        "shows it as soon as a check has finished)." + _ASK
    )


def _behind_report(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    *,
    agent: str,
    surf: str,
    policy: str,
    running: str,
    project: str = "",
) -> dict[str, Any]:
    """The report for a project that is not known to be current, within the time bound."""
    seconds = float(cfg.upgrade.start_timeout_s)
    key = _key(running, project, policy, surf, _writable(repo))
    if (kept := _cached(repo, key)) is not None:
        return kept

    def work() -> dict[str, Any]:
        out = _work(repo, log, cfg, st, agent=agent, surf=surf, policy=policy, running=running)
        out["key"] = key if _cacheable(out, surf) else ""
        return out

    def late(out: Any) -> None:
        if isinstance(out, dict) and _cacheable(out, surf):
            _remember(repo, {**out, "version": running})

    done, out = (False, None) if seconds <= 0 else _bounded(work, seconds, late)
    if done:
        return out
    return {
        "status": TIMEOUT,
        "text": _timeout_note(running, project, seconds),
        "key": key,
        "repeat": seconds > 0,  # a check the operator switched off is told once, not nagged
    }


def _project(st: State, running: str) -> str:
    """The release this project was last brought up to (`upgrade_plan.project_version`)."""
    has_history = bool(st.ddflow_versions or st.upgrades or st.items or st.sessions)
    return UP.project_version(st, has_history, running)


def _behind(st: State, running: str) -> bool:
    """Is the project older than this ddflow (or unstamped)? The cheap test: no plan."""
    project = _project(st, running)
    return not (project and not is_older(project, running))


def _decide(
    repo: Path, log: Any, cfg: Config, st: State, *, agent: str, surf: str, environ: Any
) -> dict[str, Any]:
    """What the start has to say (raises on a surprise; `start` tells it)."""
    running = running_version()
    if not (repo / ".ddflow").is_dir() or not version_key(running):
        return {"status": OFF, "text": ""}  # never adopted: a marker would adopt it
    policy = mode(cfg, environ)
    if policy == "off":
        return {"status": OFF, "text": ""}
    highest = str(getattr(st, "highest_version", "") or "")
    if highest and is_older(running, highest):
        return {"status": OLDER, "text": _older(running, highest)}
    project = _project(st, running)
    if project and not is_older(project, running):
        return {"status": CURRENT, "text": ""}
    return _behind_report(
        repo,
        log,
        cfg,
        st,
        agent=agent,
        surf=surf or surface(environ),
        policy=policy,
        running=running,
        project=project,
    )


def _remember(repo: Path, report: dict[str, Any]) -> None:
    """Keep the report for the first brief and tell the one-line notice it was said. An empty
    report is saved too when a marker exists: the project caught up (the operator applied the
    upgrade by hand), so the old proposal must not be replayed. Bookkeeping only."""
    try:
        if _writable(repo) and (report["text"] or (repo / MARKER).exists()):
            _save(repo, report)
            if report["text"]:
                UN.told(repo, report["version"], report["text"])
    except Exception:
        pass


def keep_notice(repo: Path, text: str) -> None:
    """Keep the one-line notice the brief just issued (`upgrade_notice.line`) as a proposal, so
    the briefs after it repeat it while the project is behind: on a hook-only path there was
    no start report to keep. Bookkeeping only; never raises."""
    try:
        if text:
            _remember(repo, {"status": PROPOSED, "text": text, "version": running_version()})
    except Exception:
        pass


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
    report: dict[str, Any] = {"version": running_version()}
    try:
        report.update(_decide(repo, log, cfg, st, agent=agent, surf=surf, environ=environ))
    except Exception as exc:  # the one promise: never fail the start
        report.update(
            status=FAILED,
            text=f"the upgrade check at start failed ({exc}); serving anyway. "
            "Run `ddflow upgrade --plan`.",
        )
    _remember(repo, report)
    return report


def _repeats(data: dict[str, Any]) -> bool:
    """Does the marker hold a note the brief repeats? An unfinished check or a pending proposal,
    unless the operator switched the check off (`start_timeout_s` = 0): that is said once, not
    on every brief."""
    return data.get("status") in _REPEATING and bool(data.get("repeat", True))


def _unfinished(repo: Path, st: State | None) -> str:
    """The note of a start check that reached no answer, or of a proposal still pending, while
    it still applies: the marker is of this ddflow and (when the state is known) the project is
    still behind it."""
    data = _read(repo)
    text = str(data.get("text", ""))
    if not _repeats(data) or not text:
        return ""
    running = running_version()
    if data.get("version") != running or (st is not None and not _behind(st, running)):
        return ""
    return text


def doctor_notes(repo: Path, st: State) -> list[str]:
    """For ``ddflow doctor``: the note of a start check that ran out of time or failed, or of an
    upgrade proposed and not yet applied, until the project is upgraded or a later check
    finishes. Never raises."""
    try:
        text = _unfinished(repo, st)
    except Exception:
        return []
    return [text] if text else []


def _state_of(repo: Path) -> State | None:
    """The folded state of ``repo`` (the caller had none to hand); None when unreadable."""
    try:
        cfg = Config.load(repo)
        return fold(EventLog(repo, "upgrade-start", log_cfg=cfg.log).read_all(), strict=False)
    except Exception:
        return None


def take_for_brief(repo: Path, st: State | None = None) -> str:
    """The recorded start report for the brief; "" when none or the project has caught up.

    A check that reached NO answer (a timeout, a failure) and a proposal or partial apply
    (the project still behind) are repeated by every brief while the project is still behind
    this ddflow (the folded state ``st``, read here when not given): a note that cannot be
    dropped by one reader seeing it. Anything else is said once. A marker that cannot be rewritten
    (a read-only store) still delivers the text: the cost is that the next brief repeats it,
    not that the operator is never told."""
    try:
        data = _read(repo)
        text = str(data.get("text", ""))
        if not text:
            return ""
        if _repeats(data):
            st = st if st is not None else _state_of(repo)
            if st is not None:
                return _unfinished(repo, st)
            # the state cannot be read to tell whether the project caught up: say it once,
            # like any other report, rather than not at all
        if data.get("briefed"):
            return ""
    except Exception:
        return ""
    _mark_briefed(repo, text)
    return text


def _mark_briefed(repo: Path, text: str) -> None:
    """Note that the brief said ``text``, unless the marker moved on in the meantime (under the
    lock the marker's other writers take: a late answer must not be undone)."""
    try:
        with _SAVE_LOCK:
            data = _read(repo)
            if data.get("text") == text:
                data["briefed"] = True
                fsio.atomic_write(Path(repo) / MARKER, json.dumps(data) + "\n")
    except Exception:
        pass
