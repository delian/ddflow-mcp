"""`job run|add|list|end` -- long-running processes an item is waiting on."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import ids as IDS
from ..core import outcome as O
from ..core.model import fold
from ..infra import worktree as W
from ..services import jobs as J
from ._base import _load

#: Where launched jobs write their output: under `.ddflow/local/`, which `init` ignores.
JOB_LOG_DIR = ".ddflow/local/jobs"


def _row(job, st) -> dict[str, Any]:

    s = J.status(job)
    return {
        "id": job.id,
        "item": job.item,
        "status": s.state,
        "detail": s.detail,
        "exit_code": s.exit_code,
        "pid": job.pid,
        "host": job.host,
        "command": job.command,
        "log": job.log,
        "started_at": job.started_at,
        "by": job.by,
        "note": job.note,
    }


def _not_held(log, cfg, it) -> O.Outcome | None:
    """A refusal unless the caller holds a live lease on the item.

    A job is where the RESOURCES are actually used, so starting one must pass through
    the claim that checked them. Without this an agent refused `claim` for want of GPUs
    could `job run` the training anyway -- found by driving the MCP surface end to end.

    "Live" under the same window `claim` uses (ttl + grace), and the remedy depends on
    whose lease it is: telling an agent to `claim` an item someone else holds sends it
    into a second refusal (roborev 833).
    """
    now = time.time()
    lease = it.lease
    live = lease is not None and lease.live(now, cfg.lease.grace_s)
    if live and lease.holder == log.agent_id:
        return None
    if live:
        why = (
            f"{it.id} is held by {lease.holder}, not you. Wait for it to be released, or "
            f"take other work -- a job runs under the claim of whoever does the work."
        )
    else:
        why = (
            f"{it.id} is not claimed by you. `ddflow claim {it.id}` first: the claim is "
            f"what checks its files and resources against everyone else's."
        )
    return O.refused("job.started", why, id="")


def _record(log, cfg, st, item: str, command: str, pid: int, log_path: str, cwd: str) -> str:

    parts = (item, command, str(pid))
    minted = IDS.mint(cfg, st, "job", events=log.read_all, hash_parts=parts)
    jid = minted.id
    with log.transaction():
        minted = IDS.confirm(cfg, "job", minted, used=IDS.used_now(log), hash_parts=parts)
        log.append(
            "job.started",
            jid,
            {
                **IDS.key_field(minted),
                "item": item,
                "command": command,
                "pid": pid,
                "host": J.host(),
                "proc_start": J.proc_start(pid),
                "log": log_path,
                "cwd": cwd,
            },
        )
    return jid


def job_run(
    repo: Path, item: str, command: str, *, log_file: str = "", cwd: str = "", agent: str = ""
) -> O.Outcome:
    """Launch `command` for `item`, detached, and record it.

    Runs in the item's worktree when it has one (that is where its code is), else the
    repository. Detached into its own session, so it outlives the agent, the MCP server
    and a restarted remote-control service -- which is the point of a multi-hour run.
    """

    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed("job.started", f"no such item {item!r}", id="")
    refused = _not_held(log, cfg, it)
    if refused is not None:
        return refused
    if not command.strip():
        return O.failed("job.started", "a job needs a command", id="")
    where = Path(cwd) if cwd else (W.load_path(repo, it.worktree) if it.worktree else repo)
    if not where.is_dir():
        return O.failed("job.started", f"working directory {where} does not exist", id="")
    # The log path is chosen BEFORE the id exists, so it is named for the item and a
    # stamp; the id then records it.
    # A token, not the time alone: two launches for one item in the same second shared a
    # log, truncating the first and interleaving both exit markers (roborev 835).
    out = Path(log_file) if log_file else repo / JOB_LOG_DIR / f"{item}-{clock.run_stamp()}.log"
    try:
        pid = J.launch(command, where, out)
    except RuntimeError as exc:
        return O.failed("job.started", str(exc), id="")
    jid = _record(log, cfg, st, item, command, pid, str(out), str(where))
    return O.ok("job.started", id=jid, pid=pid, log=str(out), cwd=str(where))


def job_add(
    repo: Path, item: str, pid: int, *, command: str = "", log_file: str = "", agent: str = ""
) -> O.Outcome:
    """Register a process that was started some other way (a launcher script, torchrun)."""

    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed("job.started", f"no such item {item!r}", id="")
    refused = _not_held(log, cfg, it)
    if refused is not None:
        return refused
    if pid <= 0 or not J.alive(pid):
        return O.failed(
            "job.started",
            f"no running process {pid} on {J.host()}: register a job while it runs, so its "
            f"identity (pid + start time) can be recorded",
            id="",
        )
    jid = _record(log, cfg, st, item, command, pid, log_file, "")
    return O.ok("job.started", id=jid, pid=pid, log=log_file, cwd="")


def job_list(
    repo: Path, *, item: str = "", include_ended: bool = False, agent: str = ""
) -> O.Outcome:
    """Jobs with their LIVE status, newest first. Ended jobs only with `include_ended`."""
    _log, _cfg, st = _load(repo, agent)
    jobs = sorted(st.jobs.values(), key=lambda j: j.started_at, reverse=True)
    rows = [
        _row(j, st)
        for j in jobs
        if (not item or j.item == item) and (include_ended or not j.ended_at)
    ]
    data = {"jobs": rows}
    if not rows:
        return O.nothing("job.list", "no jobs" + (f" for {item}" if item else ""), **data)
    return O.ok("job.list", **data)


def _bulk_refusal(job: str, item: str, all_finished: bool, *, single: bool) -> O.Outcome | None:
    """The refusal for a `job end` that mixes one job with the bulk flags (or names neither);
    ``single`` is any of --exit-code / --note / --force, which describe ONE job."""
    bulk = bool(item or all_finished)
    if job and bulk:
        return O.refused("job.ended", "give a job id, or --item / --all-finished, not both", id=job)
    if item and all_finished:
        return O.refused("job.ended", "--item and --all-finished contradict: pick one", id="")
    if bulk and single:
        return O.refused(
            "job.ended",
            "--exit-code, --note and --force describe ONE job: each finished job is ended with "
            "its own recorded exit, and one on another host is never ended in bulk; end that job "
            "by id to override it",
            id="",
        )
    if not job and not bulk:
        return O.failed(
            "job.ended", "name a job to end, or pass --item <id> or --all-finished", id=""
        )
    return None


def job_end(
    repo: Path,
    job: str = "",
    *,
    exit_code: int | None = None,
    note: str = "",
    force: bool = False,
    item: str = "",
    all_finished: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Record how a job ended. The exit code defaults to the one its log recorded.

    Refused while the process is still running: "ended" is a fact about the process,
    and recording it early is how a queue says a run finished that is still writing.

    With no ``job`` but ``item`` or ``all_finished``: every job whose process is over, of that
    item or of all (`job_end_finished`).
    """

    bad = _bulk_refusal(
        job, item, all_finished, single=exit_code is not None or bool(note) or force
    )
    if bad is not None:
        return bad
    if not job:
        return job_end_finished(repo, item=item, agent=agent)
    log, _cfg, st = _load(repo, agent)
    j = st.jobs.get(job)
    if j is None:
        return O.failed("job.ended", f"no such job {job!r}", id=job)
    if j.ended_at:
        return O.nothing("job.ended", f"{job} already ended", id=job)
    s = J.status(j)
    if s.state == "running":
        return O.refused(
            "job.ended",
            f"{job} is still running ({s.detail}). Stop it first, or wait.",
            id=job,
        )
    if s.state == "elsewhere" and not force:
        # "Could not look" is not "not running". Ended from another host, a run still
        # writing drops out of every brief and invites a restart (roborev 828).
        return O.refused(
            "job.ended",
            f"{job} runs on {j.host}, which cannot be checked from here. Check it there, "
            f"then pass force if it has really ended.",
            id=job,
        )
    code = exit_code if exit_code is not None else s.exit_code
    log.append("job.ended", job, {"exit_code": code, "note": note or s.detail})
    return O.ok("job.ended", id=job, exit_code=code)


def collect_finished(log, st, item: str = "") -> list[str]:
    """End every job whose process is over and whose end is determinable here (EXITED, or GONE: killed, with no exit recorded),
    for ``item`` or for all items; the ids ended. Never a running job, never one on another host
    (`status` says "elsewhere": it could not look). One append for the lot, not one per job."""
    ended: list[tuple[str, str, dict[str, Any]]] = []
    # `Job.started_at` is the `job.started` event's ISO-8601 string (records.Job: `str = ""`), never
    # a datetime or None, so the order is a plain string order.
    for j in sorted(st.jobs.values(), key=lambda x: x.started_at or ""):
        if j.ended_at or (item and j.item != item):
            continue
        s = J.status(j)
        if s.state in ("exited", "gone"):
            note = f"{s.detail} (collected)"
            ended.append(("job.ended", j.id, {"exit_code": s.exit_code, "note": note}))
    if ended:
        log.append_many(ended)
    return [subject for _kind, subject, _data in ended]


def collect_for_item(log, item: str) -> tuple[list[str], str]:
    """`collect_finished` for one item as it completes or is abandoned: (ids ended, why not).
    Never raises, because a job that cannot be checked must not make a finished item look
    unfinished; but a collection that could not run says so, it is not "no finished jobs"."""
    try:
        return collect_finished(log, fold(log.read_all(), strict=False), item), ""
    except Exception as e:
        return [], f"finished jobs of {item} were not collected: {type(e).__name__}: {e}"


def job_end_finished(repo: Path, *, item: str = "", agent: str = "") -> O.Outcome:
    """`job end --all-finished` / `--item X`: record the end of every job that is over."""
    log, _cfg, st = _load(repo, agent)
    if item and item not in st.items:
        return O.failed("job.ended", f"no such item {item!r}", ended=[])
    ended = collect_finished(log, st, item)
    where = f" of {item}" if item else ""
    if not ended:
        return O.nothing("job.ended", f"no finished jobs{where} to end", ended=[])
    return O.ok("job.ended", ended=ended, count=len(ended))
