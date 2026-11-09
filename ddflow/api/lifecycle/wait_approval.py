"""The wait loop's shared plumbing (bounded waits, lease keepalive) and the
`wait --approval` / `wait --job` verdicts + polling loops (DX-wait-approval,
DX-review-detach).

Kept out of `wait.py` so that module does not grow back toward the monolith
(`tests/test_lifecycle_layout.py` counts it under 600 lines). `wait` imports the shared
helpers and `_wait_approval` / `_wait_job` HERE; this module imports nothing back from
`wait`, so the edge is one-way.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.clock import WAIT_MAX_S
from ...core.clock import epoch as _epoch
from ...core.defaults import DEFAULT_WAIT_TIMEOUT_S
from ...core.handlers.asks import WITHDRAWN, asks_of
from ...services import jobs as J
from ...services import leasekeep as LK
from ...services import waits as WT
from ...services.ask import engine as E
from .._base import _load


def _note_cap(say, capped: bool, token: str, cap: float = WAIT_MAX_S) -> None:
    """Tell the caller its wait was shortened to the cap, and the resume token that makes
    the advice actionable: this line is what a text-mode caller reads, while `wake.token`
    is all a ``--json`` caller gets (B16e0083f21)."""
    if capped:
        say(f"waiting at most {cap}s at a time; resume with the token {token} to wait longer")


def _bounded(
    timeout_s: float | None, max_s: float | None, cap: float = WAIT_MAX_S
) -> tuple[float, bool]:
    """(seconds to wait, which a text-mode caller reads cutting the cap). ``max_s`` is the
    caller's own bound and is honoured whole: it is the wait's length when no ``timeout_s``
    is given (B-wait-lease-max-s-ignored) and the wait never outlasts it."""
    if timeout_s is None:
        timeout = DEFAULT_WAIT_TIMEOUT_S if max_s is None else float(max_s)
    else:
        timeout = float(timeout_s)
    if max_s is not None:
        return min(timeout, float(max_s)), False  # negative: refused by the caller
    return min(timeout, float(cap)), timeout > cap


def _keep_alive(log, cfg, st):
    """A tick renewing every live lease the waiter holds once per `[lease].heartbeat_s`; a
    no-op when it holds none or `[lease].renew_long_commands` is off."""
    tick, every = LK.for_holder(log, cfg, st)
    if tick is None:
        return lambda: None
    last = [time.monotonic()]

    def paced() -> None:
        if time.monotonic() - last[0] >= every:
            last[0] = time.monotonic()
            tick()

    return paced


def _poll(
    log, deadline: float, poll: float, keep_alive, repo: Path, agent: str, timer, wake_check=None
):
    """Yield a FRESH ``(cfg, st)`` each time the event log changes or the re-check cadence
    is up, until the deadline passes. Exhaustion means the caller should stop: either the
    deadline passed or ``wake_check`` (run EVERY cycle, unthrottled) turned truthy.

    Shared by the item wait and the approval wait: the sleep-keepalive-fingerprint-reload
    skeleton is one loop, not two copies (the architecture guard counts duplicates).
    ``timer`` is the caller's ``time`` MODULE (tests patch `wait.time` to skew the clock).
    """
    seen = None  # the first pass always re-judges: whatever landed since the load above
    checked = timer.monotonic()
    while True:
        left = deadline - timer.monotonic()
        if left <= 0:
            return
        timer.sleep(min(poll, left))
        keep_alive()
        if wake_check is not None and wake_check():
            return
        # Fingerprint BEFORE the read, never after: see `EventLog.mark`.
        ext = log.mark(clock=False)
        if ext == seen and timer.monotonic() - checked < WT.RECHECK_S:
            continue
        seen, checked = ext, timer.monotonic()
        log, cfg, st = _load(repo, agent)
        yield cfg, st


def _judge_approval(st, ask_id: str, now: float) -> dict[str, Any]:
    """`wait --approval`'s verdict: the one thing another agent's progress CAN end.

    Pending means waiting can help (the person may answer): blocked. Answered and approved
    means resume the gated command: ready. Not an approval (a question, which never answers
    "approved"), or denied, withdrawn, expired or unknown, is what no further waiting ends --
    refused up front with the move that WOULD help.
    """
    out: dict[str, Any] = {"why": "", "waiting_on": [], "ready": [], "blocked": []}
    row = asks_of(st).get(ask_id)
    if row is None:
        return {**out, "status": "hopeless", "why": f"no such approval request {ask_id}"}
    if row["type"] != E.APPROVAL:
        # A question, even a pending one, never answers "approved": waiting on it cannot end
        # with a spendable grant, so refuse it up front like an unknown id.
        return {**out, "status": "hopeless", "why": f"{ask_id} is not an approval request"}
    state = E.status(row, now)
    if state == E.ANSWERED:
        if row["outcome"] != E.APPROVED:
            return {
                **out,
                "status": "hopeless",
                "why": f"{ask_id} was denied by {row['answered_by']}",
            }
        if row["uses"] and row["used"] >= row["uses"]:
            return {
                **out,
                "status": "hopeless",
                "why": f"{ask_id} is spent ({row['used']} of {row['uses']} use(s))",
            }
        answered = _epoch(row["answered_at"], default=0.0)
        if row["use_ttl_s"] > 0 and now > answered + row["use_ttl_s"]:
            return {
                **out,
                "status": "hopeless",
                "why": (
                    f"{ask_id} was answered more than {row['use_ttl_s']}s ago: "
                    "request a new approval"
                ),
            }
        return {
            **out,
            "status": "ready",
            "ready": [ask_id],
            "why": f"{ask_id} is answered: re-run the gated command with --approval {ask_id}",
        }
    if state == WITHDRAWN:
        return {**out, "status": "hopeless", "why": f"{ask_id} was withdrawn"}
    if state == E.EXPIRED:
        return {**out, "status": "hopeless", "why": f"{ask_id} expired: file a new request"}
    return {
        **out,
        "status": "blocked",
        "why": f"{ask_id} is pending: the operator has not answered it yet",
        "waiting_on": [ask_id],
    }


def _approval_advice(v: dict[str, Any], ask_id: str) -> str:
    """What to do on an approval wait's end: resume the gated command, or stop. Nothing to
    claim -- the lane already holds its item and only needs the answer it asked for."""
    if v["status"] == "ready":
        return f"{ask_id} answered: re-run the gated command with --approval {ask_id}"
    if v["status"] == "hopeless":
        return f"not waiting: {v['why']}"
    return f"still pending: run `ddflow wait --approval {ask_id}` again to keep waiting"


def _approval_outcome(
    v: dict[str, Any], data: dict[str, Any], waited: float, ask_id: str
) -> O.Outcome:
    """The Outcome of an approval wait: ready (exit 0), hopeless (exit 2, waiting cannot
    help), or still pending at the deadline (exit 2, naming the wait that would resume)."""
    if v["status"] == "ready":
        return O.ok("wait", **data)
    if v["status"] == "hopeless":
        return O.nothing("wait", f"Not waiting: {v['why']}", **data)
    return O.nothing(
        "wait",
        f"Still pending after {round(waited)}s: {v['why']} Run `ddflow wait --approval "
        f"{ask_id}` again to keep waiting.",
        **data,
    )


def _wait_approval(  # noqa: PLR0913, PLR0917 -- the facets `wait` forwards
    repo: Path,
    approval: str,
    item: str,
    phase: str,
    lease: str,
    globs,
    token: str,
    timeout_s: float | None,
    poll_s: float | None,
    max_s: float | None,
    agent: str,
    on_progress,
) -> O.Outcome:
    """Block the caller (a lane) until ``approval`` is answered, renewing its own leases all
    the while. Ready when the answer is ``approved`` (re-run the gated command with
    ``--approval ID`` and spend it); refused up front -- and never slept on -- when it is
    denied, withdrawn, expired or unknown, or when the deadline passes still pending.
    """

    def empty_v(v: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": "",
            "phase": "",
            "approval": approval,
            "woke": v["status"] == "ready",
            "waitable": v["status"] != "hopeless",
            "ready": v["ready"],
            "waiting_on": v["waiting_on"],
            "freed_by": [],
            "blocked": v["blocked"],
            "waited_s": 0,
            "holders": [],
        }

    if item or phase or lease or globs or token:
        return O.failed(
            "wait",
            "approval is what to wait on instead of an item, phase, globs, lease or "
            "token: give one",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    log, cfg, st = _load(repo, agent)
    timeout, _capped = _bounded(timeout_s, max_s, cfg.lease.wait_cap_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    if timeout < 0 or poll <= 0:
        return O.failed(
            "wait",
            "timeout and max-s must be >= 0 and poll > 0 seconds",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    say = on_progress or (lambda _msg: None)

    def result(v: dict[str, Any], waited: float, freed: list[str]) -> O.Outcome:
        data = empty_v(v)
        data.update(
            waited_s=round(waited),
            freed_by=freed,
            wake={"reason": v["status"], "token": "", "messages": []},
            advice=_approval_advice(v, approval),
        )
        return _approval_outcome(v, data, waited, approval)

    v = _judge_approval(st, approval, time.time())
    if v["status"] != "blocked" or timeout == 0:
        return result(v, 0.0, [])

    started = time.monotonic()
    deadline = started + timeout
    say(f"waiting (up to {round(timeout)}s): {v['why']}")
    # The lane's OWN leases stay alive while it waits on its approval (the same keepalive
    # the item-wait uses): hours of waiting must not lapse the very claim the answer is for.
    keep_alive = _keep_alive(log, cfg, st)
    for _cfg, st in _poll(log, deadline, poll, keep_alive, repo, agent, time):
        was = v
        v = _judge_approval(st, approval, time.time())
        if v["status"] != "blocked":
            return result(v, time.monotonic() - started, [f"{approval}: {v['status']}"])
        if v["why"] != was["why"]:
            say(f"still waiting: {v['why']}")
    return result(v, time.monotonic() - started, [])  # the deadline passed still pending


def _judge_job(st, job_id: str, now: float) -> dict[str, Any]:
    """`wait --job`'s verdict: the one thing the detached review's OWN progress can end.

    A detached review (`ddflow review --detach`) relaunches the review as a recorded job
    and returns that job's id as the handle. Running means waiting can help (the review
    is still going): blocked. Over (``ended`` / ``exited`` / ``gone``) means the round is
    recorded -- a detached review records its round BEFORE its process exits -- so it is
    ready. Unknown, or running on another host (which this wait cannot poll), is what no
    further waiting ends: refused up front.
    """
    out: dict[str, Any] = {"why": "", "waiting_on": [], "ready": [], "blocked": []}
    j = st.jobs.get(job_id)
    if j is None:
        return {**out, "status": "hopeless", "why": f"no such job {job_id}"}
    s = J.status(j)
    if s.state == "running":
        return {
            **out,
            "status": "blocked",
            "why": f"{job_id} is still running ({s.detail})",
            "waiting_on": [job_id],
        }
    if s.state == "elsewhere":
        return {
            **out,
            "status": "hopeless",
            "why": f"{job_id} runs on {j.host}, which cannot be checked from here; "
            "wait there instead",
        }
    if s.state == "gone":
        # Killed with no exit recorded in its log: a detached review records its round
        # BEFORE it exits, so a process that died without writing an exit never recorded
        # one, and no further waiting records it either -- someone has to re-run the review.
        return {
            **out,
            "status": "hopeless",
            "why": f"{job_id} was killed before its round was recorded ({s.detail}); "
            "re-run the review",
        }
    return {
        **out,
        "status": "ready",
        "ready": [job_id],
        "why": f"{job_id} is finished ({s.detail}); its round is recorded",
    }


def _job_actions(j) -> list[str]:
    """What to do once a detached review's job is over: read the round it recorded, then
    act on its outcome (merge, or fix the findings)."""
    if getattr(j, "item", ""):
        return [
            f"ddflow review show {j.item}",  # triage what the round recorded
            f"ddflow gate status {j.item}",  # the gate's outcome says merge or fix
        ]
    if getattr(j, "log", ""):
        return [f"read the review's recorded answer in its log: tail {j.log}"]
    return ["read the review's recorded answer"]


def _job_advice(v: dict[str, Any], job_id: str) -> str:
    """What to do on a job wait's end: read the recorded round, or keep waiting. Nothing
    to claim -- the review already ran; only its result needs reading."""
    if v["status"] == "ready":
        return f"{job_id} was recorded: read its round and act on it (see next_actions)"
    if v["status"] == "hopeless":
        return f"not waiting: {v['why']}"
    return f"still running: run `ddflow wait --job {job_id}` again to keep waiting"


def _job_outcome(
    v: dict[str, Any], data: dict[str, Any], waited: float, job_id: str
) -> O.Outcome:
    """The Outcome of a job wait: ready (exit 0), hopeless (exit 2, waiting cannot help),
    or still running at the deadline (exit 2, naming the wait that would resume)."""
    if v["status"] == "ready":
        return O.ok("wait", **data)
    if v["status"] == "hopeless":
        return O.nothing("wait", f"Not waiting: {v['why']}", **data)
    return O.nothing(
        "wait",
        f"Still running after {round(waited)}s: {v['why']} Run `ddflow wait --job "
        f"{job_id}` again to keep waiting.",
        **data,
    )


def _wait_job(  # noqa: PLR0913, PLR0917 -- the facets `wait` forwards
    repo: Path,
    job: str,
    item: str,
    phase: str,
    lease: str,
    globs,
    token: str,
    timeout_s: float | None,
    poll_s: float | None,
    max_s: float | None,
    agent: str,
    on_progress,
) -> O.Outcome:
    """Block the caller (a lane) until ``job`` (a detached review's run handle) is over,
    renewing its own leases all the while. Ready when the job's process has finished --
    a detached review records its round before it exits, so "finished" means "recorded" --
    with ``next_actions`` naming what to do with the round. Refused up front, and never
    slept on, when the job is unknown or runs on another host, or when the deadline
    passes with it still running.
    """

    def empty_v(v: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": "",
            "phase": "",
            "job": job,
            "woke": v["status"] == "ready",
            "waitable": v["status"] != "hopeless",
            "ready": v["ready"],
            "waiting_on": v["waiting_on"],
            "freed_by": [],
            "blocked": v["blocked"],
            "waited_s": 0,
            "holders": [],
            "next_actions": [],
        }

    if item or phase or lease or globs or token:
        return O.failed(
            "wait",
            "job is what to wait on instead of an item, phase, globs, lease or token: "
            "give one",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    log, cfg, st = _load(repo, agent)
    timeout, _capped = _bounded(timeout_s, max_s, cfg.lease.wait_cap_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    if timeout < 0 or poll <= 0:
        return O.failed(
            "wait",
            "timeout and max-s must be >= 0 and poll > 0 seconds",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    say = on_progress or (lambda _msg: None)

    def result(v: dict[str, Any], waited: float, freed: list[str]) -> O.Outcome:
        j = st.jobs.get(job)
        data = empty_v(v)
        data.update(
            waited_s=round(waited),
            freed_by=freed,
            wake={"reason": v["status"], "token": "", "messages": []},
            advice=_job_advice(v, job),
            # "read the round" is an action only once a round IS recorded: it is NOT
            # next_actions while the job is still running (blocked) or never recorded one
            # (hopeless), or the CLI would print "ddflow review show X" for a round that
            # does not yet exist.
            next_actions=_job_actions(j) if j is not None and v["status"] == "ready" else [],
        )
        return _job_outcome(v, data, waited, job)

    v = _judge_job(st, job, time.time())
    if v["status"] != "blocked" or timeout == 0:
        return result(v, 0.0, [])

    started = time.monotonic()
    deadline = started + timeout
    say(f"waiting (up to {round(timeout)}s): {v['why']}")
    # The lane's OWN leases stay alive while it waits on its review (the same keepalive
    # the item-wait uses): hours of waiting must not lapse the very claim the round covers.
    keep_alive = _keep_alive(log, cfg, st)
    for _cfg, st in _poll(log, deadline, poll, keep_alive, repo, agent, time):
        was = v
        v = _judge_job(st, job, time.time())
        if v["status"] != "blocked":
            return result(v, time.monotonic() - started, [f"{job}: {v['status']}"])
        if v["why"] != was["why"]:
            say(f"still waiting: {v['why']}")
    return result(v, time.monotonic() - started, [])  # the deadline passed still running
