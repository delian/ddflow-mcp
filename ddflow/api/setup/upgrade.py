"""Upgrade this project to the running ddflow: plan, apply, restore, check, self.

The plan is the finding; an apply backs up what it rewrites first and returns the plan
that is left with an ``applied`` report.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ...core import clock
from ...core import outcome as O
from ...infra.log import running_version
from ...services import backups as BK
from ...services import selfupgrade as SU
from ...services import upgrade_apply as UA
from ...services import upgrade_check as UC
from ...services import upgrade_plan as UP
from ...services import upgrade_status as UST
from .._base import _load
from .session import _START_REPORTS

#: The wire fields of the plan: the body of `ddflow upgrade --json` and of `ddflow_upgrade`.
UPGRADE_PAYLOAD = ("running", "project_version", "up_to_date", "total", "categories")
#: ... and of an apply, which adds what it did (`applied`) to the plan that is left.
UPGRADE_APPLY_PAYLOAD = (*UPGRADE_PAYLOAD, "applied")
#: ... and of a restore, which adds what it put back (`restored`).
UPGRADE_RESTORE_PAYLOAD = (*UPGRADE_PAYLOAD, "restored")
#: ... of `--status` and `--history` (`services.upgrade_status`).
UPGRADE_STATUS_PAYLOAD = (
    "running",
    "newest",
    "newer",
    "checked_at",
    "project_version",
    "up_to_date",
    "run",
    "steps",
    "backups",
    "behind",
    "last_upgrade",
    "text",
)
UPGRADE_HISTORY_PAYLOAD = ("upgrades", "text")
_APPLIED_FIELDS = (
    "from",
    "to",
    "categories",
    "results",
    "applied",
    "refused",
    "failed",
    "unavailable",
    "backup",
    "confirmed",
    "noop",
)


def upgrade(  # noqa: PLR0913 -- one keyword per `ddflow upgrade` mode; the CLI and MCP map 1:1
    repo: Path,
    *,
    plan: bool | None = None,
    apply: str = "",
    confirm: Sequence[str] = (),
    reason: str = "",
    backup: str = "",
    snapshot: bool = False,
    restore: str = "",
    check: bool = False,
    selfup: SU.Request | None = None,
    status: bool = False,
    history: bool = False,
    resume: bool = False,
    on_progress: Callable[[str], None] | None = None,
    agent: str = "",
) -> O.Outcome:
    """What upgrading this project to the running ddflow would change (the plan, written
    nowhere), or -- with ``apply`` -- doing it (`services.upgrade_apply`).

    The plan exits 0 when the project is up to date and 1 while it has anything in it, the
    way a diff exits: the plan is the finding, and an agent checking "is there work" needs
    no parsing. ``plan`` is None when the caller said nothing (the plan), True to ask for
    it explicitly (refused together with ``apply``) and False for an apply of everything.
    An apply takes ``apply`` as the categories (a comma list, or ``all``), backs up what it will rewrite first, and returns the
    plan that is LEFT with an ``applied`` report: exit 0 when everything chosen was applied
    or acknowledged, 3 while an item waits for the operator's confirmation (``confirm`` names
    each by key, ``reason`` says why; both recorded), 1 when a step failed, 2 when one could
    not run. The body is the same on the CLI's `--json` and over MCP.

    ``check`` stands alone: it forces a release check (`services.upgrade_check`) and says the
    running version, the newest release, the project's stamp and how to upgrade; exit 0 when
    this is the newest, 1 when a newer release exists, 2 when the check is off or the index
    could not be reached.

    ``selfup`` stands alone too: it upgrades ddflow ITSELF (`services.selfupgrade`) as its
    fields say -- a dry run, a rollback, a target version, the operator's consent -- and
    exits 0 done, 1 the installer failed, 2 not attempted or nothing to roll back to, 3
    without consent.

    ``status`` and ``history`` stand alone and only read (`services.upgrade_status`): where the
    upgrade stands (exit 0 up to date, 1 while steps are pending or a run was interrupted), and
    the upgrades the log records. ``resume`` finishes an interrupted apply as ``apply`` does
    (``confirm``, ``reason`` and ``backup`` go with it), without repeating the steps it
    settled: exit 2 when no run was interrupted. ``on_progress`` hears each step as it ends.
    """
    others = _other_modes(apply, plan, confirm, reason, backup, snapshot, selfup)
    alone = (check, bool(restore), selfup is not None, status, history)
    if resume:
        if any((*alone, apply, plan is not None, snapshot)):
            return O.refused(
                "upgrade", "--resume continues an interrupted apply: nothing else beside it"
            )
        return _upgrade_resume(repo, confirm, reason, backup, on_progress, agent)
    if any(alone):
        return _upgrade_alone(repo, check, restore, selfup, others, agent, status, history)
    if snapshot:
        # `snapshot` is `backup="snapshot"` for one run; naming another mode too is a contradiction.
        if why := _snapshot_conflict(backup, bool(apply or plan is False)):
            return O.refused("upgrade", why)
        backup = "snapshot"
    if apply and plan is True:
        return O.refused("upgrade", "choose the plan or --apply, not both")
    if apply or plan is False:
        return _upgrade_apply(
            repo, apply or "all", confirm, reason, backup, agent, on_progress=on_progress
        )
    return _upgrade_plan(repo, agent)


def _upgrade_plan(repo: Path, agent: str) -> O.Outcome:
    """The plan alone: exit 0 up to date, 1 while it has anything in it."""
    log, cfg, st = _load(repo, agent)
    data = UP.build(repo, log, cfg, st)
    data["text"] = UP.render(data)
    if data["up_to_date"]:
        return O.ok("upgrade", **data)
    return O.Outcome(
        kind="upgrade",
        data=data,
        exit=O.FAIL,
        reason=f"{data['total']} upgrade item(s) pending",
    )


def _other_modes(
    apply: str,
    plan: bool | None,
    confirm: Sequence[str],
    reason: str,
    backup: str,
    snapshot: bool,
    selfup: SU.Request | None,
) -> bool:
    """Did the caller also ask for something the stand-alone modes (`--check`, `--restore`,
    `--self`) refuse beside themselves? `--self` carries its consent in `confirm` ("self") and
    `reason`: neither is another mode there."""
    keys = [c for c in confirm if not (selfup is not None and c == SU.CONSENT)]
    return bool(
        apply or plan is not None or keys or (reason and selfup is None) or backup or snapshot
    )


def _upgrade_alone(
    repo: Path,
    check: bool,
    restore: str,
    selfup: SU.Request | None,
    others: bool,
    agent: str,
    status: bool = False,
    history: bool = False,
) -> O.Outcome:
    """``--check``, ``--restore``, ``--self``, ``--status`` and ``--history`` stand alone:
    refuse them beside anything else."""
    modes = [
        n
        for n, on in (
            ("check", check),
            ("restore", restore),
            ("self", selfup),
            ("status", status),
            ("history", history),
        )
        if on
    ]
    if others or len(modes) > 1:
        return O.refused("upgrade", f"--{modes[0]} stands alone: choose it or the plan or --apply")
    if selfup is not None:
        return _upgrade_self(repo, selfup, agent)
    if status:
        return _upgrade_status(repo, agent)
    if history:
        return _upgrade_history(repo, agent)
    return _upgrade_check(repo, agent) if check else _upgrade_restore(repo, restore, agent)


def _upgrade_status(repo: Path, agent: str) -> O.Outcome:
    """`ddflow upgrade --status`: where the upgrade stands (`upgrade_status.status`)."""
    log, cfg, st = _load(repo, agent)
    data = UST.status(repo, cfg, st, log=log)
    pending = not data["up_to_date"] or bool(data["run"])  # an open run is never a finished one
    return O.Outcome(
        kind="upgrade",
        data=data,
        exit=O.FAIL if pending else O.OK,
        reason=_status_reason(data) if pending else "",
    )


def _status_reason(data: dict[str, Any]) -> str:
    """Why `--status` exits 1: steps still to do, else the run that was never finished."""
    left = sum(s["state"] != UST.DONE for s in data["steps"])
    return f"{left} upgrade step(s) pending" if left else "an upgrade run was interrupted"


def _upgrade_history(repo: Path, agent: str) -> O.Outcome:
    """`ddflow upgrade --history`: the upgrades the log records, oldest first."""
    _log, _cfg, st = _load(repo, agent)
    rows = UST.history(st)
    return O.ok("upgrade", upgrades=rows, text=UST.render_history(rows))


def _upgrade_resume(
    repo: Path,
    confirm: Sequence[str],
    reason: str,
    backup: str,
    on_progress: Callable[[str], None] | None,
    agent: str,
) -> O.Outcome:
    """`ddflow upgrade --resume`: finish the run that was interrupted, step by step."""
    _log, _cfg, st = _load(repo, agent)
    rid, run = UST.open_run(st)
    if not rid:
        return O.Outcome(
            kind="upgrade",
            data={"text": "no upgrade run was interrupted: nothing to resume"},
            exit=O.NOTHING,
            reason="nothing to resume",
        )
    if UST.run_state(run, clock.now_utc().timestamp()) == UST.RUNNING:
        return O.refused(
            "upgrade",
            f"upgrade run {rid} recorded a step less than {int(UST.LIVE_S)} s ago: it may still be "
            "applying. Wait for it, or resume once it has been quiet that long",
        )
    return _upgrade_apply(
        repo, ",".join(run["categories"]) or "all", confirm, reason, backup, agent, rid, on_progress
    )


#: The self-upgrade request the surfaces build (`services.selfupgrade.Request`).
SelfUpgrade = SU.Request

UPGRADE_SELF_PAYLOAD = (
    "status",
    "method",
    "running",
    "target",
    "to",
    "command",
    "rollback",
    "text",
)


def _upgrade_self(repo: Path, req: SU.Request, agent: str) -> O.Outcome:
    """`ddflow upgrade --self`: install a newer (or, with ``rollback``, the previous) ddflow."""
    log, cfg, _st = _load(repo, agent)
    req.newest = req.newest or UC.newer_known(repo)  # the cached answer: this asks no one
    data = SU.run(repo, cfg, log, req)
    code = data.pop("exit")
    why = {"refused": "consent needed", "manual": f"not attempted for a {data['method']} install"}
    reason = why.get(data["status"], data["status"]) if code else ""
    return O.Outcome(kind="upgrade", data=data, exit=code, reason=reason)


def _snapshot_conflict(backup: str, applying: bool) -> str:
    """Why ``--snapshot`` cannot be honoured, "" when it can."""
    if backup and backup != "snapshot":
        return f"--snapshot and --backup {backup} disagree: choose one"
    if not applying:
        return "--snapshot saves the originals of an --apply: add --apply"
    return ""


UPGRADE_CHECK_PAYLOAD = (
    "running",
    "newest",
    "newer",
    "status",
    "project_version",
    "install",
    "checked_at",
    "text",
)


def _upgrade_check(repo: Path, agent: str) -> O.Outcome:
    """`ddflow upgrade --check`: force the release check and report (`upgrade_check.report`)."""
    _log, cfg, st = _load(repo, agent)
    running = running_version()
    has_history = bool(st.ddflow_versions or st.upgrades or st.items or st.sessions)
    data = UC.report(
        repo, cfg, project_version=UP.project_version(st, has_history, running), running=running
    )
    if data["status"] in ("off", "offline"):  # no answer from the index: never "an upgrade exists"
        return O.Outcome(
            kind="upgrade", data=data, exit=O.NOTHING, reason="no release check was made"
        )
    return O.Outcome(
        kind="upgrade",
        data=data,
        exit=O.FAIL if data["newer"] else O.OK,
        reason=f"ddflow {data['newest']} is available" if data["newer"] else "",
    )


def _upgrade_apply(
    repo: Path,
    categories: str,
    confirm: Sequence[str],
    reason: str,
    backup: str,
    agent: str,
    resume: str = "",
    on_progress: Callable[[str], None] | None = None,
) -> O.Outcome:
    keys = [c.strip() for c in confirm if c and c.strip()]
    if keys and not reason.strip():
        return O.refused(
            "upgrade", "--confirm names what the operator accepts and needs --reason: say why"
        )
    log, cfg, st = _load(repo, agent)
    try:
        done = UA.apply(
            repo,
            log,
            cfg,
            st,
            categories=categories,
            confirm=dict.fromkeys(keys, reason.strip()),
            backup=backup or cfg.upgrade.backup,
            agent=agent,
            watch=UA.Watch(resume=resume, progress=on_progress),
        )
    except ValueError as exc:
        return O.failed("upgrade", str(exc))
    _START_REPORTS.pop(str(Path(repo).resolve()), None)  # the proposal it made is stale now
    log, cfg, st = _load(repo, agent)  # the plan that is left, judged on what is on disk now
    data = UP.build(repo, log, cfg, st)
    data["applied"] = {k: done[k] for k in _APPLIED_FIELDS}
    left = "Up to date." if data["up_to_date"] else f"{data['total']} item(s) remain in the plan."
    data["text"] = f"{done['text']}\n{left}".strip()
    return O.Outcome(kind="upgrade", data=data, exit=done["exit"], reason=_apply_reason(done))


def _upgrade_restore(repo: Path, name: str, agent: str) -> O.Outcome:
    """Put back what a local backup or a snapshot holds (``name``, or ``latest``)."""
    try:
        done = BK.restore(repo, name)
    except BK.SnapshotRefused as exc:
        return O.refused("upgrade", str(exc))
    except (LookupError, OSError, ValueError) as exc:
        return O.failed("upgrade", str(exc))
    _START_REPORTS.pop(
        str(Path(repo).resolve()), None
    )  # what it proposed was judged on the old files
    log, cfg, st = _load(repo, agent)
    BK.prune(repo, cfg.upgrade.backup_keep)  # the safety copy it just made counts too
    data = UP.build(repo, log, cfg, st)
    data["restored"] = done
    lines = [f"restored from {done['kind']} {done['name']}:"]
    lines += [f"  {p}" for p in done["restored"]] + [f"  removed {p}" for p in done["removed"]]
    lines.append(f"what was there is saved in {done['saved']}")
    data["text"] = "\n".join(lines)
    return O.ok("upgrade", **data)


def _apply_reason(done: dict[str, Any]) -> str:
    """Why an apply did not exit 0, in one line ("" when it did)."""
    if done["failed"]:
        return f"{done['failed']} upgrade step(s) failed"
    if done["refused"]:
        return (
            f"{done['refused']} upgrade item(s) need the operator's confirmation: "
            f"ddflow upgrade --apply --confirm KEY --reason WHY"
        )
    if done["unavailable"]:
        return f"{done['unavailable']} upgrade step(s) could not run"
    return ""
